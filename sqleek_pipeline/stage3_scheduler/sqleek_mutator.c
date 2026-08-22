/*
 * SQLeek queue filter for AFL++: skip low-value seeds via afl_custom_queue_get.
 * Path must match bind-mount: host .../output/fuzz/<dbms>_memory -> /fuzz_output
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

#define SKIP_LIST_PATH "/fuzz_output/.deferred/skip_list.txt"
#define MAX_SKIP 65536
#define MAX_NAME 512

static char skip_names[MAX_SKIP][MAX_NAME];
static int skip_count = 0;
static time_t last_reload = 0;

static void reload_skip_list(void) {
  time_t now = time(NULL);
  if (now - last_reload < 10)
    return;
  last_reload = now;

  FILE *f = fopen(SKIP_LIST_PATH, "r");
  if (!f) {
    skip_count = 0;
    return;
  }

  int n = 0;
  char line[MAX_NAME];
  while (n < MAX_SKIP && fgets(line, sizeof(line), f)) {
    size_t len = strlen(line);
    if (len > 0 && line[len - 1] == '\n')
      line[--len] = '\0';
    if (len == 0)
      continue;
    strncpy(skip_names[n], line, MAX_NAME - 1);
    skip_names[n][MAX_NAME - 1] = '\0';
    n++;
  }
  fclose(f);
  skip_count = n;
}

static int in_skip_list(const char *filename) {
  const char *base = strrchr(filename, '/');
  base = base ? base + 1 : filename;

  for (int i = 0; i < skip_count; i++) {
    if (strcmp(skip_names[i], base) == 0)
      return 1;
  }
  return 0;
}

void *afl_custom_init(void *afl, unsigned int seed) {
  (void)afl;
  (void)seed;
  reload_skip_list();
  return (void *)1;
}

void afl_custom_deinit(void *data) { (void)data; }

/* Return 0 -> AFL++ skips this queue entry for this cycle; 1 -> normal. */
uint8_t afl_custom_queue_get(void *data, const uint8_t *filename) {
  (void)data;
  reload_skip_list();
  if (in_skip_list((const char *)filename))
    return 0;
  return 1;
}
