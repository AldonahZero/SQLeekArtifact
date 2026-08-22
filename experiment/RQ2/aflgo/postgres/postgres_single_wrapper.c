#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <pwd.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static long env_long(const char *name, long defval) {
  const char *v = getenv(name);
  if (!v || !*v) return defval;
  char *end = NULL;
  long x = strtol(v, &end, 10);
  return (end && *end == '\0' && x > 0) ? x : defval;
}

static const char *env_str(const char *name, const char *defval) {
  const char *v = getenv(name);
  return (v && *v) ? v : defval;
}

static int copy_file_to_fd(const char *path, int out_fd, long limit_mb) {
  int in = open(path, O_RDONLY | O_CLOEXEC);
  if (in < 0) return -1;
  struct stat st;
  if (fstat(in, &st) != 0) {
    close(in);
    return -1;
  }
  if (st.st_size > (off_t)limit_mb * 1024 * 1024) {
    close(in);
    errno = EFBIG;
    return -1;
  }
  const char *prefix = "BEGIN;\n";
  const char *suffix = "\nROLLBACK;\n";
  if (write(out_fd, prefix, strlen(prefix)) < 0) {
    close(in);
    return -1;
  }
  char buf[65536];
  for (;;) {
    ssize_t n = read(in, buf, sizeof(buf));
    if (n == 0) break;
    if (n < 0) {
      if (errno == EINTR) continue;
      close(in);
      return -1;
    }
    ssize_t off = 0;
    while (off < n) {
      ssize_t w = write(out_fd, buf + off, (size_t)(n - off));
      if (w < 0) {
        if (errno == EINTR) continue;
        close(in);
        return -1;
      }
      off += w;
    }
  }
  if (write(out_fd, suffix, strlen(suffix)) < 0) {
    close(in);
    return -1;
  }
  close(in);
  return 0;
}

static void drop_to_postgres(void) {
  const char *user = env_str("AFLGO_POSTGRES_USER", "postgres");
  struct passwd *pw = getpwnam(user);
  if (!pw) return;
  initgroups(user, pw->pw_gid);
  setgid(pw->pw_gid);
  setuid(pw->pw_uid);
}

int main(int argc, char **argv) {
  if (argc != 2) return 2;
  const char *input = argv[1];
  const char *postgres_bin = env_str("AFLGO_POSTGRES_BIN", "/root/SQLeek/experiment/RQ2/aflgo/postgres/bin/postgres_aflgo");
  const char *datadir = env_str("AFLGO_POSTGRES_DATADIR", "/root/SQLeek/experiment/RQ2/aflgo/postgres/runtime/datadir");
  const char *dbname = env_str("AFLGO_POSTGRES_DB", "postgres");
  const char *log_path = env_str("AFLGO_POSTGRES_LOG", "/dev/null");
  long timeout_sec = env_long("AFLGO_POSTGRES_SEED_TIMEOUT", 5);
  long limit_mb = env_long("AFLGO_POSTGRES_FILE_LIMIT_MB", 16);

  int pipefd[2];
  if (pipe(pipefd) != 0) return 0;

  pid_t pid = fork();
  if (pid < 0) return 0;
  if (pid == 0) {
    setsid();
    close(pipefd[1]);
    dup2(pipefd[0], STDIN_FILENO);
    close(pipefd[0]);
    int logfd = open(log_path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    if (logfd >= 0) {
      dup2(logfd, STDOUT_FILENO);
      dup2(logfd, STDERR_FILENO);
      close(logfd);
    } else {
      int devnull = open("/dev/null", O_WRONLY);
      if (devnull >= 0) {
        dup2(devnull, STDOUT_FILENO);
        dup2(devnull, STDERR_FILENO);
        close(devnull);
      }
    }
    struct rlimit rl;
    rl.rlim_cur = rl.rlim_max = 1024;
    setrlimit(RLIMIT_NOFILE, &rl);
    drop_to_postgres();
    char *const args[] = {
      (char *)postgres_bin,
      (char *)"--single",
      (char *)"-D",
      (char *)datadir,
      (char *)"-c",
      (char *)"statement_timeout=1000",
      (char *)"-c",
      (char *)"log_min_messages=fatal",
      (char *)dbname,
      NULL
    };
    execv(postgres_bin, args);
    _exit(127);
  }

  close(pipefd[0]);
  (void)copy_file_to_fd(input, pipefd[1], limit_mb);
  close(pipefd[1]);

  time_t deadline = time(NULL) + timeout_sec;
  int status = 0;
  for (;;) {
    pid_t r = waitpid(pid, &status, WNOHANG);
    if (r == pid) break;
    if (r < 0 && errno != EINTR) break;
    if (time(NULL) >= deadline) {
      kill(-pid, SIGKILL);
      waitpid(pid, &status, 0);
      return 0;
    }
    usleep(10000);
  }
  return 0;
}
