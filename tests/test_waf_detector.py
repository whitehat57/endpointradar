from endpoint_radar.waf_detector import analyze_waf_metadata


def test_cloudflare_detection_from_cf_ray_header() -> None:
    result = analyze_waf_metadata({"cf-ray": "abc"})

    assert result.detected is True
    assert result.vendor == "Cloudflare"
    assert result.category == "cdn_or_waf"
    assert result.confidence == "high"
    assert "header: cf-ray" in result.evidence


def test_cloudflare_detection_from_cf_cache_status_header() -> None:
    result = analyze_waf_metadata({"cf-cache-status": "HIT"})

    assert result.vendor == "Cloudflare"
    assert result.confidence == "high"
    assert "header: cf-cache-status" in result.evidence


def test_cloudfront_detection_from_x_amz_cf_id_header() -> None:
    result = analyze_waf_metadata({"x-amz-cf-id": "abc"})

    assert result.detected is True
    assert result.vendor == "AWS CloudFront"
    assert result.category == "cdn"
    assert result.confidence == "medium"
    assert "header: x-amz-cf-id" in result.evidence


def test_sucuri_detection_from_x_sucuri_id_header() -> None:
    result = analyze_waf_metadata({"x-sucuri-id": "123"})

    assert result.detected is True
    assert result.vendor == "Sucuri"
    assert result.category == "waf"
    assert result.confidence == "high"
    assert "header: x-sucuri-id" in result.evidence


def test_imperva_detection_from_x_iinfo_header() -> None:
    result = analyze_waf_metadata({"x-iinfo": "1-2-3"})

    assert result.detected is True
    assert result.vendor == "Imperva / Incapsula"
    assert result.category == "waf"
    assert result.confidence == "medium"
    assert "header: x-iinfo" in result.evidence


def test_imperva_detection_from_incap_ses_cookie() -> None:
    result = analyze_waf_metadata({}, cookie_names=["incap_ses_123"])

    assert result.detected is True
    assert result.vendor == "Imperva / Incapsula"
    assert result.confidence == "medium"
    assert "cookie: incap_ses" in result.evidence


def test_unknown_result_when_no_signatures_match() -> None:
    result = analyze_waf_metadata({"server": "nginx"}, body="hello")

    assert result.detected is False
    assert result.vendor is None
    assert result.category == "unknown"
    assert result.confidence == "low"
    assert result.evidence == []


def test_generic_block_page_body_hint_returns_low_confidence_waf() -> None:
    result = analyze_waf_metadata({}, body="Request blocked by security check")

    assert result.detected is True
    assert result.vendor is None
    assert result.category == "waf"
    assert result.confidence == "low"
    assert result.evidence == ["body: generic block/challenge hint"]


def test_header_matching_is_case_insensitive() -> None:
    result = analyze_waf_metadata({"CF-Ray": "abc"})

    assert result.detected is True
    assert result.vendor == "Cloudflare"
    assert "header: cf-ray" in result.evidence


def test_vendor_specific_match_beats_generic_proxy_headers() -> None:
    result = analyze_waf_metadata({"via": "proxy", "x-cache": "HIT", "cf-ray": "abc"})

    assert result.detected is True
    assert result.vendor == "Cloudflare"
    assert result.confidence == "high"
    assert "header: via" not in result.evidence
