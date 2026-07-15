#!/usr/bin/env bash
# 一脉 Axon 统一运维控制面 —— 一键升级脚本
#
# 流程:预检 → 快照现役镜像(回滚锚点)→ 备份数据库 → 构建新镜像
#       → 跑迁移(migrate 服务:alembic upgrade head + seed)→ 重建服务
#       → 健康校验(/healthz + 前端)。任一步失败自动回滚镜像并恢复数据库。
#
# 用法:
#   ops/upgrade.sh              # 用当前工作区代码构建并升级
#   ops/upgrade.sh --pull       # 先 git pull 拉取最新代码再升级
#   ops/upgrade.sh --no-backup  # 跳过数据库备份(不建议)
#   ops/upgrade.sh --yes        # 跳过升级前确认
#
# 说明:仅升级本地 build 的应用镜像(api/worker/beat/flower/migrate/frontend);
#      postgres/redis/prometheus 等固定版本镜像不动。

set -euo pipefail

# ---- 路径:无论从何处调用,都切到仓库根(ops 的上级)----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

# ---- 参数 ----
DO_PULL=false
DO_BACKUP=true
ASSUME_YES=false
for arg in "$@"; do
  case "$arg" in
    --pull) DO_PULL=true ;;
    --no-backup) DO_BACKUP=false ;;
    --yes|-y) ASSUME_YES=true ;;
    -h|--help)
      # 只打印文件头部说明块(至首个非注释行为止),不含正文里的分隔注释
      sed -n '2,/^[^#]/{/^[^#]/d; s/^# \{0,1\}//; s/^#$//; p;}' "${BASH_SOURCE[0]}"
      exit 0 ;;
    *) echo "未知参数: $arg (用 --help 查看用法)" >&2; exit 2 ;;
  esac
done

# ---- 日志 ----
if [ -t 1 ]; then
  C_INFO=$'\033[36m'; C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_OFF=$'\033[0m'
else
  C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''; C_OFF=''
fi
log()  { echo "${C_INFO}[升级]${C_OFF} $*"; }
ok()   { echo "${C_OK}[成功]${C_OFF} $*"; }
warn() { echo "${C_WARN}[注意]${C_OFF} $*"; }
err()  { echo "${C_ERR}[失败]${C_OFF} $*" >&2; }

# ---- 依赖与 compose 命令探测 ----
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  err "未找到 docker compose,请先安装 Docker。"; exit 1
fi

[ -f docker-compose.yml ] || { err "当前目录缺少 docker-compose.yml(ROOT=$ROOT_DIR)"; exit 1; }
[ -f .env ] && { set -a; . ./.env; set +a; } || warn ".env 不存在,使用 compose 默认值(密码/密钥为默认,勿用于生产)。"

# ---- 环境变量(带默认,与 docker-compose.yml 保持一致)----
POSTGRES_USER="${POSTGRES_USER:-yimai}"
POSTGRES_DB="${POSTGRES_DB:-yimai}"
API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# 本地 build 的应用服务(升级对象);固定版本镜像的中间件不在此列
BUILT_SERVICES=(api worker beat flower migrate frontend)

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$ROOT_DIR/.backups"
BACKUP_FILE="$BACKUP_DIR/db-$TS.dump"

# ---- 回滚状态 ----
declare -A SNAP_NAME SNAP_ID   # 服务 -> 现役镜像 repo:tag / image id
SNAPSHOT_TAKEN=false
BACKUP_TAKEN=false
STAGE="init"                   # 供 trap 判断是否需要回滚

# =====================================================================
# 回滚:仅在已开始改动(构建/重建)后触发
# =====================================================================
rollback() {
  err "升级在阶段「$STAGE」失败,开始回滚……"

  if $SNAPSHOT_TAKEN; then
    log "回退应用镜像到升级前版本"
    for svc in "${!SNAP_ID[@]}"; do
      docker tag "${SNAP_ID[$svc]}" "${SNAP_NAME[$svc]}" 2>/dev/null \
        && log "  ${svc} → ${SNAP_NAME[$svc]}" \
        || warn "  ${svc} 镜像回退失败(可能镜像已被清理)"
    done
    log "用旧镜像重建服务(--no-build)"
    "${COMPOSE[@]}" up -d --no-build "${BUILT_SERVICES[@]}" >/dev/null 2>&1 || true
  fi

  if $BACKUP_TAKEN && [ -f "$BACKUP_FILE" ]; then
    warn "迁移可能已改动数据库结构,正在从备份恢复:$BACKUP_FILE"
    if "${COMPOSE[@]}" exec -T postgres \
        pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner \
        < "$BACKUP_FILE" >/dev/null 2>&1; then
      ok "数据库已恢复到升级前状态。"
    else
      err "数据库自动恢复失败!请手动执行:"
      err "  ${COMPOSE[*]} exec -T postgres pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists < $BACKUP_FILE"
    fi
  fi

  err "回滚流程结束。旧数据库备份保留在 $BACKUP_FILE,请核对服务状态。"
  exit 1
}
# 注:trap 在阶段 4(首个改动系统的步骤)前才挂载。
# 预检/快照/备份均为只读或追加操作,失败时无需回滚,让其自行退出即可。

# =====================================================================
# 1. 预检
# =====================================================================
STAGE="预检"
log "阶段 1/6:预检"
docker info >/dev/null 2>&1 || { err "Docker 守护进程未运行。"; exit 1; }

if ! $ASSUME_YES; then
  echo
  warn "即将执行升级:构建新镜像 → 迁移数据库 → 重建服务。"
  warn "对象服务:${BUILT_SERVICES[*]}"
  $DO_PULL   && warn "会先执行 git pull 拉取最新代码。"
  $DO_BACKUP && warn "会在迁移前备份数据库(失败自动回滚)。" || warn "已禁用数据库备份,失败将无法自动恢复数据!"
  read -r -p "确认继续?[y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || { log "已取消。"; trap - ERR; exit 0; }
fi

if $DO_PULL; then
  STAGE="拉取代码"
  log "git pull"
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    err "工作区有未提交改动,--pull 可能冲突。请先提交/暂存或去掉 --pull。"; exit 1
  fi
  git pull --ff-only
fi

# =====================================================================
# 2. 快照现役镜像(回滚锚点)
# =====================================================================
STAGE="镜像快照"
log "阶段 2/6:快照现役镜像(用于回滚)"
snapped=0
for svc in "${BUILT_SERVICES[@]}"; do
  id="$("${COMPOSE[@]}" images -q "$svc" 2>/dev/null | head -n1 || true)"
  [ -n "$id" ] || continue
  name="$(docker image inspect --format '{{if .RepoTags}}{{index .RepoTags 0}}{{end}}' "$id" 2>/dev/null || true)"
  [ -n "$name" ] || continue
  SNAP_ID[$svc]="$id"
  SNAP_NAME[$svc]="$name"
  snapped=$((snapped + 1))
done
if [ "$snapped" -gt 0 ]; then
  SNAPSHOT_TAKEN=true
  ok "已快照 $snapped 个现役镜像。"
else
  warn "未发现现役应用镜像(疑似首次部署),本次不做镜像回滚。"
fi

# =====================================================================
# 3. 备份数据库
# =====================================================================
STAGE="数据库备份"
if $DO_BACKUP; then
  log "阶段 3/6:备份数据库(自定义格式,便于 pg_restore)"
  if "${COMPOSE[@]}" ps --status running postgres 2>/dev/null | grep -q postgres; then
    mkdir -p "$BACKUP_DIR"
    "${COMPOSE[@]}" exec -T postgres \
      pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$BACKUP_FILE"
    BACKUP_TAKEN=true
    ok "数据库已备份:$BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
  else
    warn "postgres 未在运行(疑似首次部署),跳过备份。"
  fi
else
  warn "阶段 3/6:已用 --no-backup 跳过数据库备份。"
fi

# =====================================================================
# 4. 构建新镜像(自此开始改动系统,挂载回滚陷阱)
# =====================================================================
trap 'rollback' ERR
STAGE="构建镜像"
log "阶段 4/6:构建新镜像"
"${COMPOSE[@]}" build "${BUILT_SERVICES[@]}"
ok "镜像构建完成。"

# =====================================================================
# 5. 迁移 + 重建服务
# =====================================================================
STAGE="数据库迁移"
log "阶段 5/6:确保中间件就绪并执行迁移"
"${COMPOSE[@]}" up -d postgres redis
# migrate 服务:alembic upgrade head && seed(幂等);run --rm 会透传其退出码,失败即触发回滚
"${COMPOSE[@]}" run --rm migrate
ok "数据库迁移完成。"

STAGE="重建服务"
log "重建全部服务(新镜像 + 依赖顺序编排)"
"${COMPOSE[@]}" up -d
ok "服务已重建。"

# =====================================================================
# 6. 健康校验
# =====================================================================
STAGE="健康校验"
log "阶段 6/6:健康校验"

wait_health() {
  local url="$1" want="$2" timeout="${3:-90}" name="$4"
  local waited=0 body
  log "  等待 $name 就绪:$url"
  while [ "$waited" -lt "$timeout" ]; do
    body="$(curl -fsS --max-time 5 "$url" 2>/dev/null || true)"
    if [ -n "$body" ] && echo "$body" | grep -q "$want"; then
      ok "  $name 健康。"
      return 0
    fi
    sleep 3; waited=$((waited + 3))
  done
  err "  $name 在 ${timeout}s 内未通过健康校验。最后响应:${body:-<空>}"
  return 1
}

# 后端:/healthz 返回 {"success":true,"data":{"status":"ok",...}}
wait_health "http://localhost:${API_PORT}/healthz" '"status":"ok"' 120 "后端 API"
# 前端:nginx 返回首页 200
if curl -fsS --max-time 5 -o /dev/null "http://localhost:${FRONTEND_PORT}/"; then
  ok "  前端 健康。"
else
  err "  前端 (:${FRONTEND_PORT}) 无法访问。"
  false
fi

# =====================================================================
# 完成:清理回滚陷阱,给出后续指引
# =====================================================================
trap - ERR
STAGE="完成"
echo
ok "升级成功。"
$BACKUP_TAKEN && log "数据库备份保留在:$BACKUP_FILE(确认无误后可删除)"
log "查看状态:${COMPOSE[*]} ps"
log "查看日志:${COMPOSE[*]} logs -f api"
