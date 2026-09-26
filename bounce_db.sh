
docker rm -f ivie_pg

bash run_postgres.sh

sleep 3

bash exec_schema.sh

.venv/bin/python loader2.py

