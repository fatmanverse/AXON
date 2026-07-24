# PostgreSQL 备份恢复

脚本只接受外部 `YIMAI_DATABASE_URL`，不会读取控制面进程内连接池。备份使用 custom format，默认保留 14 天，并为每个文件生成 SHA-256 sidecar。

```bash
YIMAI_DATABASE_URL='postgresql://axon:...@db.example/axon' ops/backup/postgres-backup.sh
YIMAI_DATABASE_URL='postgresql://axon:...@db.example/axon' ops/backup/postgres-restore.sh --confirm /var/backups/axon/axon-....dump
```

恢复前必须停止 API/worker/beat，恢复到隔离数据库验证核心表和迁移版本，再切换流量。生产恢复演练应记录 RPO/RTO、备份校验结果和应用健康检查。
