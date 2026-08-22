#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>

#include "sqlite3.h"

#define MAX_SQL_INPUT (16 * 1024 * 1024)
#define DEFAULT_DB_LIMIT_MB 64
#define MAX_DB_LIMIT_MB 1024
#define SQLITE_PAGE_SIZE 4096

static char g_sidecars[4][512];

static char *read_file(const char *path, size_t *size_out) {
  FILE *fp = fopen(path, "rb");
  char *buf;
  long size;

  if (!fp) return NULL;
  if (fseek(fp, 0, SEEK_END) != 0) {
    fclose(fp);
    return NULL;
  }
  size = ftell(fp);
  if (size < 0 || size > MAX_SQL_INPUT) {
    fclose(fp);
    return NULL;
  }
  rewind(fp);

  buf = (char *)malloc((size_t)size + 1);
  if (!buf) {
    fclose(fp);
    return NULL;
  }
  if (size > 0 && fread(buf, 1, (size_t)size, fp) != (size_t)size) {
    free(buf);
    fclose(fp);
    return NULL;
  }
  fclose(fp);
  buf[size] = '\0';
  *size_out = (size_t)size;
  return buf;
}

static long db_limit_mb(void) {
  const char *raw = getenv("AFLGO_SQLITE_DB_LIMIT_MB");
  char *end = NULL;
  long mb;

  if (!raw || !*raw) return DEFAULT_DB_LIMIT_MB;
  errno = 0;
  mb = strtol(raw, &end, 10);
  if (errno != 0 || !end || *end != '\0' || mb <= 0 || mb > MAX_DB_LIMIT_MB) {
    return DEFAULT_DB_LIMIT_MB;
  }
  return mb;
}

static void configure_file_limit(void) {
  struct rlimit limit;
  rlim_t bytes = (rlim_t)db_limit_mb() * 1024 * 1024;

  limit.rlim_cur = bytes;
  limit.rlim_max = bytes;
  setrlimit(RLIMIT_FSIZE, &limit);
}

static void init_db_paths(char *db_path, size_t db_path_size) {
  const char *suffixes[] = {"", "-journal", "-wal", "-shm"};
  const char *tmpdir = getenv("AFLGO_SQLITE_TMPDIR");
  size_t i;

  if (!tmpdir || tmpdir[0] != '/' || strlen(tmpdir) > 240) {
    tmpdir = "/tmp";
  }
  snprintf(db_path, db_path_size, "%s/aflgo_sqlite_%ld.db", tmpdir, (long)getpid());
  for (i = 0; i < sizeof(suffixes) / sizeof(suffixes[0]); i++) {
    snprintf(g_sidecars[i], sizeof(g_sidecars[i]), "%s%s", db_path, suffixes[i]);
  }
}

static void cleanup_db(void) {
  size_t i;

  for (i = 0; i < sizeof(g_sidecars) / sizeof(g_sidecars[0]); i++) {
    if (g_sidecars[i][0]) unlink(g_sidecars[i]);
  }
}

static void signal_cleanup(int sig) {
  cleanup_db();
  _exit(sig == SIGXFSZ ? 0 : 128 + sig);
}

static void install_signal_handlers(void) {
  struct sigaction action;
  int signals[] = {SIGTERM, SIGINT, SIGHUP, SIGALRM, SIGXFSZ};
  size_t i;

  memset(&action, 0, sizeof(action));
  action.sa_handler = signal_cleanup;
  sigemptyset(&action.sa_mask);
  for (i = 0; i < sizeof(signals) / sizeof(signals[0]); i++) {
    sigaction(signals[i], &action, NULL);
  }
}

static void configure_sqlite_limits(sqlite3 *db) {
  char pragmas[256];
  char *errmsg = NULL;
  long max_pages = (db_limit_mb() * 1024L * 1024L) / SQLITE_PAGE_SIZE;

  sqlite3_limit(db, SQLITE_LIMIT_LENGTH, MAX_SQL_INPUT);
  sqlite3_limit(db, SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_INPUT);
  snprintf(pragmas, sizeof(pragmas),
           "PRAGMA journal_mode=OFF;"
           "PRAGMA synchronous=OFF;"
           "PRAGMA temp_store=MEMORY;"
           "PRAGMA page_size=%d;"
           "PRAGMA max_page_count=%ld;",
           SQLITE_PAGE_SIZE, max_pages);
  sqlite3_exec(db, pragmas, NULL, NULL, &errmsg);
  sqlite3_free(errmsg);
}

int main(int argc, char **argv) {
  sqlite3 *db = NULL;
  char *sql = NULL;
  char *errmsg = NULL;
  char db_path[256];
  size_t sql_size = 0;

  if (argc != 2) return 0;

  configure_file_limit();
  init_db_paths(db_path, sizeof(db_path));
  install_signal_handlers();
  cleanup_db();

  sql = read_file(argv[1], &sql_size);
  if (!sql || sql_size == 0) {
    free(sql);
    return 0;
  }

  if (sqlite3_open(db_path, &db) == SQLITE_OK) {
    configure_sqlite_limits(db);
    sqlite3_exec(db, sql, NULL, NULL, &errmsg);
    sqlite3_free(errmsg);
  }

  if (db) sqlite3_close(db);
  cleanup_db();
  free(sql);
  return 0;
}
