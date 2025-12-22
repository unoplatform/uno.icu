#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unicode/utypes.h>
#include <unicode/udata.h>
#include <unicode/ubrk.h>
#include <unicode/ubidi.h>
#include <unicode/uversion.h>

int loaded_icu_data = false;

char* uno_udata_setCommonData(const void* bytes) {
    UErrorCode errorCode = U_ZERO_ERROR;
    udata_setCommonData(bytes, &errorCode);
    return U_FAILURE(errorCode) ? u_errorName(errorCode) : NULL;
}

UBreakIterator* init_line_breaker(UChar* chars) {
    UErrorCode status = U_ZERO_ERROR;

    UBreakIterator *breaker = ubrk_open(UBRK_LINE, NULL, chars, -1, &status);
    if (U_FAILURE(status)) {
        fprintf(stderr, "Failed to create break iterator: %s\n", u_errorName(status));
        return NULL;
    }

    ubrk_first(breaker);
    return breaker;
}

int32_t next_line_breaking_opportunity(UBreakIterator *breaker) {
    int32_t next = ubrk_next(breaker);
    if (next == UBRK_DONE) {
        ubrk_close(breaker);
        return -1; // UBRK_DONE is -1 already, but this is done explicitly for clarity
    } else {
        return next;
    }
}

// The below are wrappers for the ICU functions used by Uno. This is necessary for
// NativeAOT where these symbols, which are normally read from __Internal, fail
// to be found, so we provide our own with a prefix to avoid conflicts.

UBiDi* uno_ubidi_open(void) {
    return ubidi_open();
}

void uno_ubidi_close(UBiDi *pBiDi) {
    ubidi_close(pBiDi);
}

void uno_ubidi_setPara(UBiDi *pBiDi, const UChar *text, int32_t length, UBiDiLevel paraLevel, UBiDiLevel *embeddingLevels, UErrorCode *pErrorCode) {
    ubidi_setPara(pBiDi, text, length, paraLevel, embeddingLevels, pErrorCode);
}

void uno_ubidi_getLogicalRun(const UBiDi *pBiDi, int32_t logicalPosition, int32_t *pLogicalLimit, UBiDiLevel *pLevel) {
    ubidi_getLogicalRun(pBiDi, logicalPosition, pLogicalLimit, pLevel);
}

int32_t uno_ubidi_countRuns(UBiDi *pBiDi, UErrorCode *pErrorCode) {
    return ubidi_countRuns(pBiDi, pErrorCode);
}

UBiDiDirection uno_ubidi_getVisualRun(UBiDi *pBiDi, int32_t runIndex, int32_t *pLogicalStart, int32_t *pLength) {
    return ubidi_getVisualRun(pBiDi, runIndex, pLogicalStart, pLength);
}

void uno_u_getVersion(UVersionInfo versionArray) {
    u_getVersion(versionArray);
}

void uno_u_versionToString(const UVersionInfo versionArray, char *versionString) {
    u_versionToString(versionArray, versionString);
}

// TEST CODE: DO NOT COMPILE THIS IN
// int main(int argc, char **argv) {
//     UChar* text = u"Hello ragaa";
// 	UBreakIterator* breaker = init_line_breaker(text);
//     for (int next_line_break = 0;;next_line_break = next_line_breaking_opportunity(breaker))
//     {
//         printf("next_line_breaking_opportunity: %d\n", next_line_break);
//         if (next_line_break == -1) break;
//     }
//     return EXIT_SUCCESS;
// }
