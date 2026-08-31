from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "android_manifest_sample" / "AndroidManifest.xml"


def _manifest_chunks():
    from app.services.parser import chunk_source

    source = FIXTURE_PATH.read_text(encoding="utf-8")
    return source, chunk_source("app/src/main/AndroidManifest.xml", "xml", source)


def test_android_manifest_structurally_chunks_permissions_and_root_elements():
    source, chunks = _manifest_chunks()
    by_symbol = {chunk.symbol: chunk for chunk in chunks}

    assert len(source.splitlines()) == 41
    assert (by_symbol["manifest:com.example.security"].start_line, by_symbol["manifest:com.example.security"].end_line) == (2, 3)
    camera = by_symbol["uses-permission:android.permission.CAMERA"]
    assert camera.chunk_type == "xml_permission"
    assert (camera.start_line, camera.end_line) == (4, 4)
    assert camera.content == '    <uses-permission android:name="android.permission.CAMERA" />'
    declared = by_symbol["permission:com.example.security.ADMIN_PERMISSION"]
    assert (declared.start_line, declared.end_line) == (5, 7)
    assert 'android:protectionLevel="signature"' in declared.content
    assert "uses-feature:android.hardware.camera" in by_symbol
    assert "application:.SecurityApplication" in by_symbol


def test_android_manifest_components_preserve_attributes_context_and_exact_lines():
    _, chunks = _manifest_chunks()
    by_symbol = {chunk.symbol: chunk for chunk in chunks}

    activity = by_symbol["activity:.MainActivity"]
    assert activity.chunk_type == "xml_component"
    assert (activity.start_line, activity.end_line) == (13, 21)
    assert 'android:exported="true"' in activity.content
    assert 'android:permission="com.example.security.ADMIN_PERMISSION"' in activity.content
    assert "<intent-filter>" in activity.content
    assert 'android.intent.action.MAIN' in activity.content

    assert (by_symbol["activity-alias:.MainAlias"].start_line, by_symbol["activity-alias:.MainAlias"].end_line) == (22, 25)
    assert (by_symbol["service:.AccountService"].start_line, by_symbol["service:.AccountService"].end_line) == (26, 29)
    assert (by_symbol["receiver:.AccountReceiver"].start_line, by_symbol["receiver:.AccountReceiver"].end_line) == (30, 32)
    provider = by_symbol["provider:.DataProvider"]
    assert (provider.start_line, provider.end_line) == (33, 39)
    for attribute in ["android:authorities", "android:readPermission", "android:writePermission", "android:grantUriPermissions", "android:exported"]:
        assert attribute in provider.content


def test_android_namespace_prefix_can_vary_without_losing_attribute_identity():
    from app.services.parser import chunk_source

    source = """<manifest xmlns:a="http://schemas.android.com/apk/res/android" package="example">
    <uses-permission a:name="android.permission.CAMERA" />
    <application>
        <service a:name=".CameraService" a:permission="android.permission.CAMERA" a:exported="false" />
    </application>
</manifest>
"""
    chunks = chunk_source("AndroidManifest.xml", "xml", source)
    by_symbol = {chunk.symbol: chunk for chunk in chunks}

    assert "uses-permission:android.permission.CAMERA" in by_symbol
    assert "service:.CameraService" in by_symbol
    assert 'a:permission="android.permission.CAMERA"' in by_symbol["service:.CameraService"].content


def test_android_manifest_reuses_generic_security_tagging():
    _, chunks = _manifest_chunks()
    permission = next(chunk for chunk in chunks if chunk.symbol == "uses-permission:android.permission.CAMERA")
    provider = next(chunk for chunk in chunks if chunk.symbol == "provider:.DataProvider")

    assert "potential_access_check" in permission.tags
    assert "potential_config_file" in permission.tags
    assert "potential_access_check" in provider.tags


def test_malformed_android_manifest_falls_back_without_crashing():
    from app.services.parser import chunk_source

    source = "<manifest>\n<application>\n<activity android:name=\".Broken\">\n"
    chunks = chunk_source("AndroidManifest.xml", "xml", source)

    assert chunks
    assert all(chunk.chunk_type == "line_range_fallback" for chunk in chunks)
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 3)
    assert chunks[0].content == source.rstrip("\n")


def test_non_manifest_xml_keeps_existing_generic_fallback():
    from app.services.parser import chunk_source

    chunks = chunk_source("res/xml/network_security_config.xml", "xml", "<network-security-config>\n</network-security-config>\n")

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "line_range_fallback"
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)


def test_android_manifest_real_index_path_persists_structural_chunks_and_navigation(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    indexed_payloads = []
    monkeypatch.setattr(project_service, "index_code_chunk", lambda chunk: indexed_payloads.append(chunk.copy()) or f"code:{chunk['id']}")
    project = project_service.create_project(ProjectCreate(name="Manifest fixture", source_type="github"))
    repository = Path(project["local_path"])
    target = repository / "app" / "src" / "main" / "AndroidManifest.xml"
    target.parent.mkdir(parents=True)
    target.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    project_service.index_project(project["id"])
    with db() as connection:
        file_row = connection.execute("SELECT * FROM files WHERE project_id = ?", (project["id"],)).fetchone()
        rows = connection.execute(
            "SELECT chunk_type, symbol_name, start_line, end_line, code FROM code_chunks WHERE project_id = ?",
            (project["id"],),
        ).fetchall()

    symbols = {row["symbol_name"] for row in rows}
    activity = next(row for row in rows if row["symbol_name"] == "activity:.MainActivity")
    assert file_row["file_path"] == "app/src/main/AndroidManifest.xml"
    assert file_row["language"] == "xml"
    assert {"uses-permission:android.permission.CAMERA", "activity:.MainActivity", "provider:.DataProvider"}.issubset(symbols)
    assert (activity["chunk_type"], activity["start_line"], activity["end_line"]) == ("xml_component", 13, 21)
    assert "<intent-filter>" in activity["code"]
    assert any(item["language"] == "xml" and item["symbol_name"] == "provider:.DataProvider" for item in indexed_payloads)
    opened = project_service.file_content(project["id"], "app/src/main/AndroidManifest.xml")
    assert opened["language"] == "xml"
    assert 'android:authorities="com.example.security.data"' in opened["content"]
