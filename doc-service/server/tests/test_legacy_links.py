from app.services.legacy_links import convert_legacy_href, rewrite_legacy_markdown


def test_relative_and_version_files():
    assert convert_legacy_href("ors_e49e8488-v1.0.html", product="ors") == "/ors/m/ors_e49e8488?v=v1.0"
    assert convert_legacy_href("./biz.html", product="usp") == "/usp/m/biz"


def test_cross_product_uses_catalog():
    catalog = {"usp": {"biz"}, "ors": {"ors_3a7f10c2"}}
    assert (
        convert_legacy_href("ors_3a7f10c2.html", product="usp", ids_by_product=catalog)
        == "/ors/m/ors_3a7f10c2"
    )
    assert convert_legacy_href("biz.html", product="ors", ids_by_product=catalog) == "/usp/m/biz"


def test_old_host_absolute_urls():
    catalog = {"usp": {"biz"}, "ors": set()}
    assert (
        convert_legacy_href("http://10.10.10.202:8766/m/biz.html", product="usp", ids_by_product=catalog)
        == "/usp/m/biz"
    )
    assert (
        convert_legacy_href(
            "http://10.10.10.202:8766/req.html?id=req_4112e95c",
            product="usp",
        )
        == "/usp/requirements/req_4112e95c"
    )


def test_req_relative_and_external_kept():
    assert convert_legacy_href("../req.html?id=req_7fea4f45", product="ors") == "/ors/requirements/req_7fea4f45"
    assert convert_legacy_href("https://example.com/x.html", product="usp") == "https://example.com/x.html"
    png = "data:image/png;base64," + ("A" * 8000)
    assert convert_legacy_href(png, product="usp") == png
    assert convert_legacy_href("/api/v1/inline-media/ab", product="usp") == "/api/v1/inline-media/ab"


def test_version_file_alias():
    assert (
        convert_legacy_href(
            "./um_6e3357f6.html",
            product="usp",
            module_id="um_5a6603dd",
            versions=["v1.0", "v1.1"],
            current_version="v1.1",
            known_ids={"um_5a6603dd", "biz"},
        )
        == "/usp/m/um_5a6603dd?v=v1.0"
    )


def test_numbered_legacy_markdown_files():
    catalog = {
        "usp": {
            "sv_plan",
            "mv_plan",
            "mv_cent",
            "biz",
            "school_as",
            "charge",
            "charge_busy",
            "env_ext",
            "beh_ext",
            "beh_assign_prio",
            "lmapf",
        },
        "ors": set(),
    }
    assert convert_legacy_href("17-charge.md", product="usp", ids_by_product=catalog) == "/usp/m/charge"
    assert convert_legacy_href("./16-biz-assign.md", product="usp", ids_by_product=catalog) == "/usp/m/biz"
    assert convert_legacy_href("13-mapf.md", product="usp", ids_by_product=catalog) == "/usp/m/mv_cent"
    assert convert_legacy_href("05-mv.md", product="usp", ids_by_product=catalog) == "/usp/m/mv_plan"
    assert convert_legacy_href("04-sv.md", product="usp", ids_by_product=catalog) == "/usp/m/sv_plan"
    assert convert_legacy_href("06-assign.md", product="usp", ids_by_product=catalog) == "/usp/m/school_as"
    assert convert_legacy_href("./30-env.md", product="usp", ids_by_product=catalog) == "/usp/m/env_ext"
    assert convert_legacy_href("17a-charge-busy.md", product="usp", ids_by_product=catalog) == "/usp/m/charge_busy"
    assert convert_legacy_href("14-lmapf.md", product="usp", ids_by_product=catalog) == "/usp/m/lmapf"
    assert (
        convert_legacy_href(
            "13-mapf.md",
            product="usp",
            module_id="lmapf",
            versions=["v1.0", "v1.1"],
            current_version="v1.1",
            ids_by_product=catalog,
        )
        == "/usp/m/mv_cent"
    )


def test_markdown_rewrites_source():
    catalog = {"usp": {"biz"}, "ors": {"ors_3a7f10c2"}}
    out = rewrite_legacy_markdown(
        "见[意图识别](ors_3a7f10c2.html)与[业务](http://10.10.10.202:8766/m/biz.html)。",
        product="usp",
        ids_by_product=catalog,
    )
    assert "/ors/m/ors_3a7f10c2" in out
    assert "/usp/m/biz" in out
    assert "8766" not in out
    assert ".html" not in out
