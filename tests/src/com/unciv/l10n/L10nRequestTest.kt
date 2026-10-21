package com.unciv.l10n

import org.junit.Assert.*
import org.junit.Test

class L10nRequestTest {
    private val state = """{
      "schema_version":1,"save":"issue-8-pair","selection":{"technology":"Archery"},
      "tab":null,"expanded_sections":[],"scroll":{},
      "scroll_to":{"tech-tree":{"technology":"Mining","align":"visible"}},
      "camera":null,"new_game":null
    }"""

    private fun rejected(json: String = state, screen: String = "tech-picker", token: String = "test-1") {
        assertThrows(IllegalArgumentException::class.java) { L10nRequest.parse(screen, token, json) }
    }

    @Test fun acceptsAnAnchorWithoutLanguageOrShellEscapingFields() {
        val request = L10nRequest.parse("tech-picker", "capture-003", state)
        assertEquals("issue-8-pair", request.save)
        assertEquals("Archery", request.selection.getString("technology"))
    }

    @Test fun rejectsUnknownScreenAndUncorrelatableToken() {
        rejected(screen = "typo")
        rejected(token = "request with spaces")
    }

    @Test fun rejectsMissingSaveAndPathTraversal() {
        rejected(state.replace("\"issue-8-pair\"", "null"))
        rejected(state.replace("issue-8-pair", "../other"))
    }

    @Test fun rejectsUnknownDuplicateAndMissingFields() {
        rejected(state.replace("\"schema_version\":1", "\"schema_version\":1,\"language\":\"German\""))
        rejected(state.replace("\"schema_version\":1", "\"schema_version\":1,\"schema_version\":1"))
        rejected(state.replace("\"camera\":null,", ""))
    }

    @Test fun rejectsWrongSelectionAndTabInsteadOfOpeningASimilarScreen() {
        rejected(state.replace("technology", "unit"))
        rejected(state.replace("\"tab\":null", "\"tab\":\"our-status\""))
    }

    @Test fun rejectsConflictingScrollInstructionsAndInvalidOffsets() {
        rejected(state.replace("\"scroll\":{}", "\"scroll\":{\"tech-tree\":{\"x\":0,\"y\":0}}"))
        rejected(state.replace("\"scroll\":{}", "\"scroll\":{\"description\":{\"x\":0,\"y\":-1}}"))
    }

    @Test fun rejectsUnknownOrAmbiguousAnchorSyntax() {
        rejected(state.replace("\"technology\":\"Mining\",\"align\":\"visible\"", "\"technology\":\"Mining\",\"text\":\"Mining\",\"align\":\"visible\""))
        rejected(state.replace("visible", "nearby"))
        rejected(state.replace("\"technology\":\"Mining\"", "\"edge\":\"outside\""))
    }

    @Test fun supportsArticleNamesWithSpacesAndUnicode() {
        val json = state.replace("\"technology\":\"Archery\"", "\"category\":\"Tutorial\",\"article\":\"Differences from Civilization V — ü\"")
        assertEquals("Differences from Civilization V — ü", L10nRequest.parse("civilopedia", "article-1", json).selection.getString("article"))
    }
}
