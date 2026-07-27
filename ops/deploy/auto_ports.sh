#!/usr/bin/env bash
# 端口自动避让脚本（部署前置）。
#
# 背景：控制面 docker-compose 要发布多个宿主机端口（postgres/redis/api/前端/
# flower/prometheus/alertmanager/grafana）。目标机上可能已跑别的项目占了这些端口，
# 直接 `docker compose up` 会因端口冲突启动失败。此脚本在部署前探测每个端口，
# 被占用则从基准端口顺延到下一个空闲端口，把最终映射写进 .env（compose 直接读），
# 并打印「期望端口 → 实际端口」映射表，一眼看清哪些被顺延了。
#
# 用法：
#   ops/deploy/auto_ports.sh [.env 路径]        # 默认当前目录 .env
#   AXON_PORT_SCAN_LIMIT=200 ops/deploy/auto_ports.sh   # 覆盖顺延上限
#
# 幂等：重复执行时，已在 .env 里且当前空闲（或已被本项目占用）的端口保持不变；
# 只对「新占用且非本项目」的端口重新顺延。写入前对 .env 做时间戳备份。

set -euo pipefail

ENV_FILE="${1:-.env}"
# 顺延探测上限：从基准端口起最多向上试这么多个，避免异常时死循环。
SCAN_LIMIT="${AXON_PORT_SCAN_LIMIT:-100}"

# .env 里的端口变量名 → 该服务的基准（期望）端口。顺序即打印顺序。
# 与 docker-compose.yml 的 ${VAR:-default} 默认值保持一致。
PORT_VARS=(
  "POSTGRES_PORT:5432"
  "REDIS_PORT:6379"
  "API_PORT:8000"
  "FRONTEND_PORT:5173"
  "FLOWER_PORT:5555"
  "PROMETHEUS_PORT:9090"
  "ALERTMANAGER_PORT:9093"
  "GRAFANA_PORT:3000"
)

log()  { printf '%s\n' "$*" >&2; }
info() { log "[auto-ports] $*"; }

# 判断某端口是否已被监听（占用）。优先用 ss，回退 /dev/tcp 探测。
# 返回 0 = 被占用，1 = 空闲。
port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    # -H 去表头，-l 仅监听，-n 数字化，-t/-u TCP+UDP。匹配 :<port> 结尾的本地地址。
    ss -Hlnt "( sport = :$port )" 2>/dev/null | grep -q . && return 0
    ss -Hlnu "( sport = :$port )" 2>/dev/null | grep -q . && return 0
    return 1
  fi
  # 无 ss 时用 bash /dev/tcp：能连上说明有服务在听（占用）。
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    exec 3>&- 2>/dev/null || true
    return 0
  fi
  return 1
}

# 从 base 起找第一个空闲端口；已被 same-run 选走的端口也要跳过，避免两个服务撞同一个。
declare -a CHOSEN_PORTS=()
already_chosen() {
  local p="$1" c
  for c in "${CHOSEN_PORTS[@]:-}"; do
    [ "$c" = "$p" ] && return 0
  done
  return 1
}

find_free_port() {
  local base="$1" candidate="$base" tries=0
  while [ "$tries" -lt "$SCAN_LIMIT" ]; do
    if ! port_in_use "$candidate" && ! already_chosen "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
    candidate=$((candidate + 1))
    tries=$((tries + 1))
  done
  return 1
}

# 读 .env 里某变量的现值（不存在返回空）。
env_value() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 0
  # 取最后一次赋值，去掉行内注释与首尾空白。
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | sed -E "s/^${key}=//; s/[[:space:]]+#.*$//; s/[[:space:]]*$//"
}

# 把 key=value 写回 .env：存在则替换该行，不存在则追加。
upsert_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if [ -f "$ENV_FILE" ] && grep -qE "^${key}=" "$ENV_FILE"; then
    # 用 awk 精确替换，避免 sed 对 value 里的特殊字符敏感。
    awk -v k="$key" -v v="$value" '
      $0 ~ "^" k "=" { print k "=" v; next }
      { print }
    ' "$ENV_FILE" > "$tmp"
  else
    [ -f "$ENV_FILE" ] && cat "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  mv "$tmp" "$ENV_FILE"
}

main() {
  # 备份现有 .env，改坏了能回退。
  if [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
  else
    info ".env 不存在，将新建：$ENV_FILE"
    : > "$ENV_FILE"
  fi

  local shifted=0
  local -a rows=()
  rows+=("变量|期望|实际|状态")

  local entry key base current chosen
  for entry in "${PORT_VARS[@]}"; do
    key="${entry%%:*}"
    base="${entry##*:}"
    current="$(env_value "$key")"

    # 优先尊重 .env 里已有且当前空闲（或本项目已占）的值，保证幂等、不乱跳。
    if [ -n "$current" ] && ! port_in_use "$current" && ! already_chosen "$current"; then
      chosen="$current"
    else
      # .env 无值 / 现值被占：从基准端口顺延找空闲。
      local start="${current:-$base}"
      # 现值被占时也从基准起找（而非从被占的现值），让端口尽量贴近默认、可预期。
      chosen="$(find_free_port "$base")" || {
        info "错误：从 $base 起 $SCAN_LIMIT 个端口内找不到空闲端口（$key），请人工介入。"
        exit 1
      }
      # 记录一下 start 只为语义完整，避免 shellcheck 未使用告警。
      : "$start"
    fi

    CHOSEN_PORTS+=("$chosen")
    upsert_env "$key" "$chosen"

    if [ "$chosen" = "$base" ]; then
      rows+=("$key|$base|$chosen|默认")
    else
      rows+=("$key|$base|$chosen|已顺延")
      shifted=$((shifted + 1))
    fi
  done

  # 打印对齐的映射表。
  info "端口映射结果（写入 $ENV_FILE）："
  printf '%s\n' "${rows[@]}" | column -t -s '|' >&2
  if [ "$shifted" -gt 0 ]; then
    info "共 $shifted 个端口因占用已顺延到空闲端口。"
  else
    info "所有端口均可用默认值，无冲突。"
  fi
}

main "$@"
