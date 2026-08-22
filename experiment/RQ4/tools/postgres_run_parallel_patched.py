import re
import time
import os
import shutil
import subprocess
import atexit
import getopt
import sys
import signal

postgres_root_dir = os.environ.get("SQLEEK_POSTGRES_ROOT", "/opt/dbms")
postgres_src_data_dir = os.path.join(postgres_root_dir, "data_all/ori_data")
current_workdir = os.getcwd()

starting_core_id = int(os.environ.get("SQLEEK_START_CORE", "0"))
parallel_num = int(os.environ.get("SQLEEK_NUM_CONCURRENT", "1"))
port_starting_num = int(os.environ.get("SQLEEK_PORT_START", "7000"))

sqleek_dbms_name = os.environ.get("SQLEEK_DBMS", "postgres")
sqleek_input_dir = os.environ.get("SQLEEK_INPUT_DIR", "./inputs")
sqleek_afl_bin = os.environ.get("SQLEEK_SQLRIGHT_AFL", "./afl-fuzz")
sqleek_timeout_ms = os.environ.get("SQLEEK_AFL_TIMEOUT", "2000")
sqleek_memory_limit = os.environ.get("SQLEEK_MEMORY_LIMIT", "2000")
sqleek_log_dir = os.environ.get("SQLEEK_LOG_DIR", os.environ.get("LOG_DIR", ""))
sqleek_postgres_bin = os.environ.get("SQLEEK_POSTGRES_BIN", os.path.join(postgres_root_dir, "bin/postgres"))
sqleek_postgres_host = os.environ.get("SQLEEK_POSTGRES_HOST", "127.0.0.1")
sqleek_afl_sync_id = os.environ.get("SQLEEK_AFL_SYNC_ID", "")
sqleek_pg_log_min_messages = os.environ.get("SQLEEK_PG_LOG_MIN_MESSAGES", "")
sqleek_pg_client_min_messages = os.environ.get("SQLEEK_PG_CLIENT_MIN_MESSAGES", "")
sqleek_pg_log_error_verbosity = os.environ.get("SQLEEK_PG_LOG_ERROR_VERBOSITY", "")
sqleek_pg_suppress_server_log = os.environ.get("SQLEEK_PG_SUPPRESS_SERVER_LOG", "0")

all_fuzzing_p_list = []
all_postgres_p_list = []
all_log_files = []
shm_env_list = []


def terminate_children():
    for proc in list(all_fuzzing_p_list) + list(all_postgres_p_list):
        if proc is None or proc.poll() is not None:
            continue
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    deadline = time.time() + 10
    for proc in list(all_fuzzing_p_list) + list(all_postgres_p_list):
        if proc is None or proc.poll() is not None:
            continue
        timeout = max(0, deadline - time.time())
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    for fp in all_log_files:
        try:
            fp.flush()
            fp.close()
        except Exception:
            pass


def signal_handler(_signum, _frame):
    terminate_children()
    sys.exit(0)


atexit.register(terminate_children)
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def check_pid(pid: int):
    """Check whether pid are still running."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def chown_postgres(path: str):
    if os.geteuid() != 0:
        return
    try:
        subprocess.check_call(["chown", "-R", "postgres:postgres", path])
    except Exception as exc:
        print(f"Warning: failed to chown {path}: {exc}", flush=True)


# Parse the command line arguments:
output_dir_str = ""
oracle_str = "NOREC"
feedback_str = ""

try:
    opts, args = getopt.getopt(sys.argv[1:], "o:c:n:O:F:", ["odir=", "start-core=", "num-concurrent=", "oracle=", "feedback="])
except getopt.GetoptError:
    print("Arguments parsing error")
    exit(1)
for opt, arg in opts:
    if opt in ("-o", "--odir"):
        output_dir_str = arg
        print("Using output dir: %s" % (output_dir_str))
    elif opt in ("-c", "--start-core"):
        starting_core_id = int(arg)
        print("Using starting_core_id: %d" % (starting_core_id))
    elif opt in ("-n", "--num-concurrent"):
        parallel_num = int(arg)
        print("Using num-concurrent: %d" % (parallel_num))
    elif opt in ("-O", "--oracle"):
        oracle_str = arg
        print("Using oracle: %s " % (oracle_str))
    elif opt in ("-F", "--feedback"):
        feedback_str = arg
        print("Using feedback: %s " % (feedback_str))
    else:
        print("Error. Input arguments not supported. \n")
        exit(1)

sys.stdout.flush()

if os.path.isfile(os.path.join(os.getcwd(), "shm_env.txt")):
    os.remove(os.path.join(os.getcwd(), "shm_env.txt"))

for cur_inst_id in range(starting_core_id, starting_core_id + parallel_num, 1):
    print("Setting up core_id: " + str(cur_inst_id))

    # Set up the PostgreSQL data folder first.
    cur_postgre_data_dir_str = os.path.join(postgres_root_dir, "data_all/data_" + str(cur_inst_id - starting_core_id))
    if os.path.isdir(cur_postgre_data_dir_str):
        shutil.rmtree(cur_postgre_data_dir_str)
    shutil.copytree(postgres_src_data_dir, cur_postgre_data_dir_str)
    chown_postgres(cur_postgre_data_dir_str)

    # Set up SQLRight output folder.
    if os.environ.get("SQLEEK_OUTPUT_LAYOUT", "") == "1":
        cur_output_dir_str = os.path.join(output_dir_str, sqleek_dbms_name + "_memory", "default")
    elif output_dir_str != "":
        cur_output_dir_str = output_dir_str + "/outputs_" + str(cur_inst_id - starting_core_id)
    else:
        cur_output_dir_str = "./outputs/outputs_" + str(cur_inst_id - starting_core_id)
    os.makedirs(cur_output_dir_str, exist_ok=True)
    cur_afl_output_dir_str = cur_output_dir_str
    if sqleek_afl_sync_id:
        # AFL -S stores this instance under <sync_dir>/<sync_id>.
        cur_afl_output_dir_str = os.path.dirname(cur_output_dir_str)

    child_log_dir = sqleek_log_dir or cur_output_dir_str
    os.makedirs(child_log_dir, exist_ok=True)
    cur_output_file = open(os.path.join(child_log_dir, "sqlright_postgresql_child_%d.log" % cur_inst_id), "w", buffering=1)
    all_log_files.append(cur_output_file)

    # Prepare for env shared by the fuzzer and postgres.
    cur_port_num = port_starting_num + cur_inst_id - starting_core_id

    fuzzing_command = [
        sqleek_afl_bin,
        "-t", sqleek_timeout_ms,
        "-m", sqleek_memory_limit,
        "-P", str(cur_port_num),
        "-i", sqleek_input_dir,
        "-o", cur_afl_output_dir_str,
        "-c", str(cur_inst_id),
        "-O", oracle_str,
    ]

    if feedback_str != "":
        fuzzing_command += ["-F", feedback_str]

    if sqleek_afl_sync_id:
        fuzzing_command += ["-S", sqleek_afl_sync_id]

    fuzzing_command.append("aaa")

    modi_env = os.environ.copy()
    modi_env["AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES"] = "1"
    modi_env["AFL_SKIP_CPUFREQ"] = "1"
    modi_env["LD_LIBRARY_PATH"] = os.environ.get("SQLEEK_SQLRIGHT_CLIENT_LD_LIBRARY_PATH", "/usr/lib/x86_64-linux-gnu")

    print("Running fuzzing command: " + " ".join(fuzzing_command))

    p = subprocess.Popen(
        fuzzing_command,
        cwd=os.getcwd(),
        shell=False,
        stderr=cur_output_file,
        stdout=cur_output_file,
        stdin=subprocess.DEVNULL,
        env=modi_env,
    )
    all_fuzzing_p_list.append(p)

    # Read the current generated shm_mem_id.
    shm_path = os.path.join(os.getcwd(), "shm_env.txt")
    for _ in range(120):
        if os.path.isfile(shm_path):
            break
        if p.poll() is not None:
            raise RuntimeError("SQLRight afl-fuzz exited before shm_env.txt was created")
        time.sleep(1)
    if not os.path.isfile(shm_path):
        raise RuntimeError("Timed out waiting for shm_env.txt from SQLRight afl-fuzz")
    with open(shm_path) as shm_env_fd:
        cur_shm_str = shm_env_fd.read().strip()
    shm_env_list.append(cur_shm_str)
    os.remove(shm_path)

    # Start the PostgreSQL instance.
    postgres_log = None
    postgres_stdio = subprocess.DEVNULL
    if sqleek_pg_suppress_server_log not in ("1", "true", "TRUE", "yes", "YES"):
        postgres_log = open(os.path.join(child_log_dir, "postgres_core_%d.log" % cur_inst_id), "w", buffering=1)
        all_log_files.append(postgres_log)
        postgres_stdio = postgres_log

    postgre_command = [
        sqleek_postgres_bin,
        "-D", cur_postgre_data_dir_str,
        "-p", str(cur_port_num),
        "-h", sqleek_postgres_host,
    ]
    if sqleek_pg_log_min_messages:
        postgre_command += ["-c", f"log_min_messages={sqleek_pg_log_min_messages}"]
    if sqleek_pg_client_min_messages:
        postgre_command += ["-c", f"client_min_messages={sqleek_pg_client_min_messages}"]
    if sqleek_pg_log_error_verbosity:
        postgre_command += ["-c", f"log_error_verbosity={sqleek_pg_log_error_verbosity}"]
    if os.geteuid() == 0:
        postgre_command = ["runuser", "-u", "postgres", "--"] + postgre_command

    postgre_env = os.environ.copy()
    postgre_env["__AFL_SHM_ID"] = cur_shm_str
    postgre_env["LD_LIBRARY_PATH"] = os.environ.get("SQLEEK_POSTGRES_LD_LIBRARY_PATH", "/opt/dbms/lib")

    print("Running postgres command: __AFL_SHM_ID=" + cur_shm_str + " " + " ".join(postgre_command), end="\n\n")
    p = subprocess.Popen(
        postgre_command,
        cwd=postgres_root_dir,
        shell=False,
        stderr=postgres_stdio,
        stdout=postgres_stdio,
        stdin=subprocess.DEVNULL,
        env=postgre_env,
    )
    all_postgres_p_list.append(p)

    sys.stdout.flush()

print("Finished launching the fuzzing. ")
sys.stdout.flush()

while True:
    # Keep this wrapper alive so SIGTERM can clean up AFL and PostgreSQL.
    for proc in list(all_fuzzing_p_list):
        if proc.poll() is not None:
            print("SQLRight afl-fuzz exited with rc=%s" % proc.returncode, flush=True)
            terminate_children()
            sys.exit(proc.returncode or 0)
    time.sleep(10)
