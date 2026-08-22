#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define MAX_SQL_INPUT (16 * 1024 * 1024)
#define DEFAULT_TIMEOUT_SEC 8
#define DEFAULT_FILE_LIMIT_MB 128

static char g_sql_path[512];
static char g_socket_path[512];
static char g_pid_path[512];
static pid_t g_child = -1;

static long env_long(const char *name, long fallback, long min_value, long max_value) {
  const char *raw = getenv(name);
  char *end = NULL;
  long value;
  if (!raw || !*raw) return fallback;
  errno = 0;
  value = strtol(raw, &end, 10);
  if (errno != 0 || !end || *end != '\0' || value < min_value || value > max_value) return fallback;
  return value;
}

static int copy_input(FILE *out, const char *path) {
  FILE *in = fopen(path, "rb");
  char buf[8192];
  size_t total = 0;
  if (!in) return -1;
  while (!feof(in)) {
    size_t n = fread(buf, 1, sizeof(buf), in);
    if (n > 0) {
      total += n;
      if (total > MAX_SQL_INPUT) { fclose(in); return -1; }
      if (fwrite(buf, 1, n, out) != n) { fclose(in); return -1; }
    }
    if (ferror(in)) { fclose(in); return -1; }
  }
  fclose(in);
  return total > 0 ? 0 : -1;
}

static int prepare_sql_file(const char *input_path) {
  const char *tmpdir = getenv("AFLGO_MARIADB_TMPDIR");
  FILE *out;
  if (!tmpdir || tmpdir[0] != '/' || strlen(tmpdir) > 240) tmpdir = "/tmp";
  snprintf(g_sql_path, sizeof(g_sql_path), "%s/aflgo_mariadb_%ld.sql", tmpdir, (long)getpid());
  out = fopen(g_sql_path, "wb");
  if (!out) return -1;
  fputs("DROP DATABASE IF EXISTS aflgo_fuzz;\n"
        "CREATE DATABASE aflgo_fuzz;\n"
        "USE aflgo_fuzz;\n"
        "SET sql_log_bin=0;\n",
        out);
  if (copy_input(out, input_path) != 0) {
    fclose(out);
    unlink(g_sql_path);
    g_sql_path[0] = '\0';
    return -1;
  }
  fputs("\nDROP DATABASE IF EXISTS aflgo_fuzz;\n", out);
  if (fclose(out) != 0) {
    unlink(g_sql_path);
    g_sql_path[0] = '\0';
    return -1;
  }
  return 0;
}

static void cleanup(void) {
  if (g_child > 0) {
    kill(-g_child, SIGKILL);
    kill(g_child, SIGKILL);
  }
  if (g_sql_path[0]) unlink(g_sql_path);
  if (g_socket_path[0]) unlink(g_socket_path);
  if (g_pid_path[0]) unlink(g_pid_path);
}

static void on_signal(int sig) {
  cleanup();
  _exit(128 + sig);
}

static void install_handlers(void) {
  struct sigaction action;
  int signals[] = {SIGTERM, SIGINT, SIGHUP, SIGALRM};
  size_t i;
  memset(&action, 0, sizeof(action));
  action.sa_handler = on_signal;
  sigemptyset(&action.sa_mask);
  for (i = 0; i < sizeof(signals) / sizeof(signals[0]); i++) sigaction(signals[i], &action, NULL);
}

static void set_file_limit(void) {
  long mb = env_long("AFLGO_MARIADB_FILE_LIMIT_MB", DEFAULT_FILE_LIMIT_MB, 1, 4096);
  struct rlimit limit;
  limit.rlim_cur = (rlim_t)mb * 1024 * 1024;
  limit.rlim_max = (rlim_t)mb * 1024 * 1024;
  setrlimit(RLIMIT_FSIZE, &limit);
}

static int run_mariadbd(void) {
  const char *mysqld = getenv("AFLGO_MARIADBD");
  const char *datadir = getenv("AFLGO_MARIADB_DATADIR");
  const char *basedir = getenv("AFLGO_MARIADB_BASEDIR");
  const char *charset_dir = getenv("AFLGO_MARIADB_CHARSET_DIR");
  const char *tmpdir = getenv("AFLGO_MARIADB_TMPDIR");
  const char *log_error = getenv("AFLGO_MARIADB_LOG_ERROR");
  int fd, status = 0;
  long timeout_sec = env_long("AFLGO_MARIADB_SEED_TIMEOUT", DEFAULT_TIMEOUT_SEC, 1, 120);
  if (!mysqld || !datadir || !basedir || !charset_dir) return 0;
  if (!tmpdir || tmpdir[0] != '/') tmpdir = "/tmp";
  if (!log_error || log_error[0] != '/') log_error = "/tmp/aflgo_mariadb.err";
  snprintf(g_socket_path, sizeof(g_socket_path), "%s/aflgo_mariadb_%ld.sock", tmpdir, (long)getpid());
  snprintf(g_pid_path, sizeof(g_pid_path), "%s/aflgo_mariadb_%ld.pid", tmpdir, (long)getpid());
  g_child = fork();
  if (g_child < 0) return 0;
  if (g_child == 0) {
    char *args[20];
    char datadir_arg[512], basedir_arg[512], charset_arg[512], socket_arg[512], pid_arg[512], tmp_arg[512], log_arg[512];
    int idx = 0;
    setpgid(0, 0);
    set_file_limit();
    setenv("AFL_NO_FORKSRV", "1", 1);
    fd = open(g_sql_path, O_RDONLY);
    if (fd < 0) _exit(0);
    dup2(fd, STDIN_FILENO);
    close(fd);
    snprintf(datadir_arg, sizeof(datadir_arg), "--datadir=%s", datadir);
    snprintf(basedir_arg, sizeof(basedir_arg), "--basedir=%s", basedir);
    snprintf(charset_arg, sizeof(charset_arg), "--character-sets-dir=%s", charset_dir);
    snprintf(socket_arg, sizeof(socket_arg), "--socket=%s", g_socket_path);
    snprintf(pid_arg, sizeof(pid_arg), "--pid-file=%s", g_pid_path);
    snprintf(tmp_arg, sizeof(tmp_arg), "--tmpdir=%s", tmpdir);
    snprintf(log_arg, sizeof(log_arg), "--log-error=%s", log_error);
    args[idx++] = (char *)mysqld;
    args[idx++] = (char *)"--no-defaults";
    args[idx++] = (char *)"--bootstrap";
    args[idx++] = (char *)"--skip-networking";
    args[idx++] = (char *)"--skip-grant-tables";
    args[idx++] = (char *)"--default-storage-engine=MEMORY";
    args[idx++] = datadir_arg;
    args[idx++] = basedir_arg;
    args[idx++] = charset_arg;
    args[idx++] = socket_arg;
    args[idx++] = pid_arg;
    args[idx++] = tmp_arg;
    args[idx++] = log_arg;
    args[idx] = NULL;
    execv(mysqld, args);
    _exit(0);
  }
  alarm((unsigned int)timeout_sec);
  while (waitpid(g_child, &status, 0) < 0 && errno == EINTR) {}
  alarm(0);
  kill(-g_child, SIGKILL);
  g_child = -1;
  return 0;
}

int main(int argc, char **argv) {
  if (argc != 2) return 0;
  install_handlers();
  if (prepare_sql_file(argv[1]) != 0) return 0;
  run_mariadbd();
  cleanup();
  return 0;
}
