"""Hebrew/English address normalization."""
import unittest
from . import _helpers
from address_norm import normalize


class TestNormalize(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize("   "), "")

    def test_lowercase_and_trim(self):
        self.assertEqual(normalize("  Tel Aviv  "), "tel aviv")

    def test_collapse_whitespace(self):
        self.assertEqual(normalize("a    b\t\tc"), "a b c")

    def test_punctuation_stripped(self):
        self.assertEqual(normalize("a, b. c! d?"), "a b c d")

    def test_niqqud_removed(self):
        # דִּיזֶנְגּוֹף with niqqud → דיזנגוף
        self.assertEqual(normalize("דִּיזֶנְגּוֹף"), "דיזנגוף")

    def test_hebrew_variants_collapse_to_one_key(self):
        variants = [
            "דיזנגוף 50, תל-אביב",
            "דיזנגוף 50  ,  תל אביב",
            'דִּיזֶנְגּוֹף 50, תל אביב',
            'רחוב דיזנגוף 50, ת"א',
        ]
        normalized = {normalize(v) for v in variants}
        self.assertEqual(len(normalized), 1, f"expected 1 key, got {normalized}")

    def test_abbreviation_expansion(self):
        self.assertEqual(normalize('ת"א'), "תל אביב")
        self.assertIn("רמת גן", normalize("ר'ג"))
        self.assertIn("פתח תקווה", normalize("פ'ת"))
        self.assertIn("באר שבע", normalize("ב'ש"))

    def test_rehov_prefix_stripped(self):
        self.assertNotIn("רחוב", normalize("רחוב דיזנגוף 50"))

    def test_unicode_nfkc_normalisation(self):
        # Half-width digit + full-width digit normalize to the same
        self.assertEqual(normalize("דיזנגוף 50"), normalize("דיזנגוף ５０"))


if __name__ == "__main__":
    unittest.main()
