#include <stdio.h>
#include <unicode/ustring.h>
#include <unicode/utypes.h>
#include <emscripten/emscripten.h>
#include <emscripten/em_js.h>

EM_JS(void, unoicu_set_result, (int ok, int status), {
    Module.unoicuResult = { ok: Boolean(ok), status: status };
});

static void report_result(UBool ok, int32_t status) {
    unoicu_set_result(ok ? 1 : 0, status);
}

int main(void) {
    UChar buffer[32];
    UErrorCode status = U_ZERO_ERROR;
    const char *hello = "Uno ICU";
    int32_t required = 0;

    u_strFromUTF8(buffer, (int32_t)(sizeof(buffer) / sizeof(buffer[0])), &required, hello, -1, &status);
    if (U_FAILURE(status)) {
        report_result(false, status);
        fprintf(stderr, "u_strFromUTF8 failed: %d\n", status);
        return status;
    }

    printf("Converted string length: %d\n", required);
    report_result(true, status);
    return 0;
}
