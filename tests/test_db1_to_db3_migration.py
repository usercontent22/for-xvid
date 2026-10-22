import unittest

from scripts.migrate_db1_to_db3 import fingerprint, limit_tags


class Db1ToDb3MigrationTests(unittest.TestCase):
    def test_limits_tags_to_first_ten_in_source_order(self):
        tags = [
            "ass", "babysitter", "blond", "blondes", "boyfriend",
            "brunette", "amateur", "couple", "outdoor", "interracial",
            "extra-tag", "another-tag",
        ]
        self.assertEqual(limit_tags(tags), tags[:10])
        self.assertEqual(len(limit_tags(tags)), 10)

    def test_short_or_empty_tag_arrays_are_preserved(self):
        self.assertEqual(limit_tags(["blond", "brunette"]), ["blond", "brunette"])
        self.assertEqual(limit_tags([]), [])
        self.assertEqual(limit_tags(None), [])

    def test_pooled_and_direct_urls_fingerprint_to_same_database(self):
        pooled = "postgresql://user:pw@ep-example-pooler.us-east-1.aws.neon.tech/app"
        direct = "postgresql://user:pw@ep-example.us-east-1.aws.neon.tech/app"
        self.assertEqual(fingerprint(pooled), fingerprint(direct))


if __name__ == "__main__":
    unittest.main()
