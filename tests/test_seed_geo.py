import random
import unittest

from scripts.seed_synthetic import (
    CITY_PROFILES,
    ColumnSpec,
    build_entities,
    county_name_for,
    value_for,
)


class SeedGeoTest(unittest.TestCase):
    def test_profiles_cover_enum_examples(self):
        cities = [item.city_zj for item in CITY_PROFILES]
        self.assertEqual(
            cities,
            [
                "杭州地区",
                "宁波地区",
                "温州地区",
                "嘉兴地区",
                "湖州地区",
                "绍兴地区",
                "金华地区",
                "衢州地区",
                "舟山地区",
                "台州地区",
                "丽水地区",
            ],
        )
        counties = [county for item in CITY_PROFILES for county in item.counties]
        self.assertEqual(len(counties), len(set(counties)))
        self.assertIn("金华本部", counties)
        self.assertIn("宁波本部", counties)
        jinhua = next(item for item in CITY_PROFILES if item.city_zj == "金华地区")
        ningbo = next(item for item in CITY_PROFILES if item.city_zj == "宁波地区")
        self.assertNotIn("宁波本部", jinhua.counties)
        self.assertIn("宁波本部", ningbo.counties)
        self.assertEqual(jinhua.counties, ("金华本部", "义乌", "兰溪", "浦江"))

    def test_entities_keep_city_county_pairs(self):
        entities = build_entities(120, random.Random(1))
        allowed = {item.city_zj: set(item.counties) for item in CITY_PROFILES}
        for entity in entities:
            self.assertIn(entity.county_zj, allowed[entity.city_zj])
            if entity.city_zj == "金华地区":
                self.assertNotEqual(entity.county_zj, "宁波本部")

    def test_enum_columns_do_not_cross_city(self):
        city_enums = [item.city_zj for item in CITY_PROFILES]
        county_enums = [county for item in CITY_PROFILES for county in item.counties]
        entity = build_entities(11, random.Random(2))[6]
        city_col = ColumnSpec("city_zj", "varchar", "地市简称", city_enums)
        county_col = ColumnSpec("county_zj", "varchar", "区县简称", county_enums)
        peer = entity
        city_value = value_for(city_col, entity=entity, peer=peer, ym="202501", row_id=0, rng=random.Random(0))
        county_value = value_for(county_col, entity=entity, peer=peer, ym="202501", row_id=0, rng=random.Random(0))
        profile = next(item for item in CITY_PROFILES if item.city_zj == city_value)
        self.assertEqual(city_value, entity.city_zj)
        self.assertEqual(county_value, entity.county_zj)
        self.assertIn(county_value, profile.counties)

    def test_county_company_name(self):
        self.assertEqual(county_name_for("国网金华供电公司", "金华本部"), "国网金华供电公司")
        self.assertEqual(county_name_for("国网金华供电公司", "义乌"), "国网义乌供电公司")


if __name__ == "__main__":
    unittest.main()
