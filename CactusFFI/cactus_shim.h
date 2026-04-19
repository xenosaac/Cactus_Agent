#ifndef CACTUS_SHIM_H
#define CACTUS_SHIM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void* cactus_model_t;
typedef void* cactus_stream_transcribe_t;

typedef void (*cactus_token_callback)(const char* token, uint32_t token_id, void* user_data);

cactus_model_t cactus_init(const char* model_path, const char* corpus_dir, bool cache_index);
void cactus_destroy(cactus_model_t model);
const char* cactus_get_last_error(void);
void cactus_log_set_level(int level);

cactus_stream_transcribe_t cactus_stream_transcribe_start(
    cactus_model_t model,
    const char* options_json
);

int cactus_stream_transcribe_process(
    cactus_stream_transcribe_t stream,
    const uint8_t* pcm_buffer,
    size_t pcm_buffer_size,
    char* response_buffer,
    size_t buffer_size
);

int cactus_stream_transcribe_stop(
    cactus_stream_transcribe_t stream,
    char* response_buffer,
    size_t buffer_size
);

int cactus_complete(
    cactus_model_t model,
    const char* messages_json,
    char* response_buffer,
    size_t buffer_size,
    const char* options_json,
    const char* tools_json,
    cactus_token_callback callback,
    void* user_data,
    const uint8_t* pcm_buffer,
    size_t pcm_buffer_size
);

#ifdef __cplusplus
}
#endif

#endif
