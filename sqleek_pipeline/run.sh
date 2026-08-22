docker rm -f sqleek_*_postgres

nohup bash /root/SQLeek/sqleek_pipeline/run_all.sh --skip-stage1 --skip-stage2 --duration 0 --dbms postgres \
  > /root/SQLeek/sqleek_pipeline/output/online_run.log 2>&1 </dev/null &
disown

tmux at -t fuzz