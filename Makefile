# 一脉 Axon 统一运维控制面 — 常用命令(T0.8)
# 约定:开发期后端用 uv、前端用 npm;全栈联调用 docker-compose。

.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up down logs ps restart build upgrade \
        backend-install backend-dev backend-test backend-lint migrate seed \
        frontend-install frontend-dev frontend-test frontend-build frontend-lint \
        test lint

help: ## 列出所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---- 全栈(docker-compose)----
up: ## 一键拉起全栈(postgres/redis/后端/前端/监控三件套)
	$(COMPOSE) up -d --build

down: ## 停止并移除全部容器
	$(COMPOSE) down

logs: ## 跟随查看全部服务日志
	$(COMPOSE) logs -f

ps: ## 查看服务状态
	$(COMPOSE) ps

restart: ## 重启全部服务
	$(COMPOSE) restart

build: ## 仅构建镜像
	$(COMPOSE) build

upgrade: ## 一键升级(打包镜像→备份→迁移→重建→健康校验,失败自动回滚)
	./ops/upgrade.sh

# ---- 后端(本地开发)----
backend-install: ## 安装后端依赖(uv)
	cd backend && uv sync

backend-dev: ## 本地起后端(热重载)
	cd backend && uv run uvicorn app.main:app --reload --port 8000

backend-test: ## 跑后端测试 + 覆盖率
	cd backend && uv run pytest tests/ --cov=app

backend-lint: ## 后端 lint + format 检查
	cd backend && uv run ruff check app tests && uv run black --check app tests

migrate: ## 执行数据库迁移到最新
	cd backend && uv run alembic upgrade head

seed: ## 灌入种子管理员
	cd backend && uv run python -m app.cli.seed

# ---- 前端(本地开发)----
frontend-install: ## 安装前端依赖
	cd frontend && npm install

frontend-dev: ## 本地起前端(Vite dev server)
	cd frontend && npm run dev

frontend-test: ## 跑前端测试
	cd frontend && npm run test

frontend-build: ## 构建前端产物
	cd frontend && npm run build

frontend-lint: ## 前端 lint
	cd frontend && npm run lint

# ---- API 契约与类型生成(T0.11)----
gen-api: ## 导出后端 OpenAPI 并生成前端 TS 类型
	cd backend && uv run python -m app.cli.export_openapi
	cd frontend && npm run gen:api

# ---- 聚合 ----
test: backend-test frontend-test ## 跑全部测试

lint: backend-lint frontend-lint ## 跑全部 lint
