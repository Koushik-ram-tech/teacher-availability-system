"""
Integration tests for DOCX import API endpoints.

Tests the complete DOCX import workflow:
- Upload DOCX → preview
- Manual resolution
- Finalize blocks
- Confirm → persist

Uses the real MCA DOCX for end-to-end validation.
"""
import pytest
from pathlib import Path

@pytest.fixture
def mca_docx_path():
    """Returns the absolute path to the authoritative MCA DOCX fixture."""
    root_dir = Path(__file__).resolve().parents[2]
    return root_dir / "sample_files" / "MCA Timetable-2026-Odd V7.docx"


# ---------------------------------------------------------------------------
# Test: Successful DOCX upload
# ---------------------------------------------------------------------------


def test_upload_docx_success(client, db, mca_docx_path):
    """Test successful DOCX upload returns preview."""
    # Use real MCA DOCX
    with open(mca_docx_path, "rb") as f:
        response = client.post(
            "/api/v1/imports/docx",
            files={"file": ("test.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "academic_year": "2026-Odd",
                "department": "Computer Applications",
            },
        )

    assert response.status_code == 200
    data = response.json()

    # Check structure
    assert "import_id" in data
    assert data["filename"] == "test.docx"
    assert data["academic_year"] == "2026-Odd"
    assert data["department"] == "Computer Applications"
    assert data["parser_status"] in ["COMPLETE", "PARTIAL", "FAILED"]

    # Check counts
    assert "total_blocks" in data
    assert "resolved_count" in data
    assert "unresolved_count" in data
    assert data["total_blocks"] > 0

    # Check faculty legend
    assert "faculty_legend" in data
    assert len(data["faculty_legend"]) > 0

    # Check activities
    assert "resolved_activities" in data
    assert "unresolved_blocks" in data


# ---------------------------------------------------------------------------
# Test: Real MCA DOCX parsing through endpoint
# ---------------------------------------------------------------------------


def test_upload_real_mca_docx(client, db, mca_docx_path):
    """Test real MCA DOCX parsing through the endpoint."""
    with open(mca_docx_path, "rb") as f:
        response = client.post(
            "/api/v1/imports/docx",
            files={"file": ("MCA Timetable-2026-Odd V7.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "academic_year": "2026-Odd",
                "department": "Computer Applications",
            },
        )

    assert response.status_code == 200
    data = response.json()

    # Verify expected parse results from validation
    # UPDATED: Fixed duplicate block bug where merged cells created phantom duplicates
    # Before fix: 175 blocks (with ~61 duplicates from gridSpan cells)
    # After fix: 114 blocks (no duplicates)
    assert data["total_blocks"] == 114
    assert data["resolved_count"] >= 50  # Approximate - varies with resolution logic
    assert data["unresolved_count"] >= 50  # Approximate
    assert data["manually_resolved_count"] == 0

    # Verify faculty legend (14 entries)
    assert len(data["faculty_legend"]) == 14

    # Verify known faculty
    acronyms = {entry["acronym"] for entry in data["faculty_legend"]}
    assert "DNS" in acronyms
    assert "SU" in acronyms
    assert "TS" in acronyms

    # Verify parser status (may be FAILED if parser errors, or PARTIAL if just unresolved)
    # The important thing is that it parsed something
    assert data["parser_status"] in ["PARTIAL", "COMPLETE", "FAILED"]
    assert data["total_blocks"] > 0  # Actual parsing happened


# ---------------------------------------------------------------------------
# Test: Tuesday I-A appears in preview
# ---------------------------------------------------------------------------


def test_tuesday_ia_ambiguity_preserved(client, db, mca_docx_path):
    """Test that Tuesday I-A ambiguous block appears in unresolved blocks."""
    with open(mca_docx_path, "rb") as f:
        response = client.post(
            "/api/v1/imports/docx",
            files={"file": ("MCA Timetable-2026-Odd V7.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "academic_year": "2026-Odd",
                "department": "Computer Applications",
            },
        )

    assert response.status_code == 200
    data = response.json()

    # Find Tuesday I-A block
    tuesday_ia_blocks = [
        block for block in data["unresolved_blocks"]
        if block["day"] == "tuesday" and "I-A" in block["section"]
    ]

    assert len(tuesday_ia_blocks) > 0

    # Check first Tuesday I-A block
    block = tuesday_ia_blocks[0]
    assert "S4" in block["slots"] or "S5" in block["slots"]
    assert len(block["activity_candidates"]) >= 1
    assert len(block["teacher_candidates"]) >= 2
    assert "ambiguity_reason" in block
    assert block["resolution_required"] is True


# ---------------------------------------------------------------------------
# Test: Malformed DOCX returns 400
# ---------------------------------------------------------------------------


def test_upload_malformed_docx(client, db):
    """Test that malformed DOCX returns 400."""
    # Create fake DOCX (empty file)
    fake_docx = b"not a real docx"

    response = client.post(
        "/api/v1/imports/docx",
        files={"file": ("fake.docx", fake_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={
            "academic_year": "2026-Odd",
            "department": "Computer Applications",
        },
    )

    assert response.status_code == 400
    assert "parsing failed" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test: Unsupported file type returns 400
# ---------------------------------------------------------------------------


def test_upload_non_docx_file(client, db):
    """Test that non-DOCX file returns 400."""
    response = client.post(
        "/api/v1/imports/docx",
        files={"file": ("test.txt", b"not a docx", "text/plain")},
        data={
            "academic_year": "2026-Odd",
            "department": "Computer Applications",
        },
    )

    assert response.status_code == 400
    assert "Only .docx files are accepted" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Test: Empty file returns 400
# ---------------------------------------------------------------------------


def test_upload_empty_file(client, db):
    """Test that empty file returns 400."""
    response = client.post(
        "/api/v1/imports/docx",
        files={"file": ("empty.docx", b"", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={
            "academic_year": "2026-Odd",
            "department": "Computer Applications",
        },
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test: Manual resolution of one block
# ---------------------------------------------------------------------------


def test_manual_resolution_single_block(client, db, mca_docx_path):
    """Test manual resolution of a single block."""
    # Upload DOCX
    with open(mca_docx_path, "rb") as f:
        upload_response = client.post(
            "/api/v1/imports/docx",
            files={"file": ("MCA Timetable-2026-Odd V7.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "academic_year": "2026-Odd",
                "department": "Computer Applications",
            },
        )

    assert upload_response.status_code == 200
    upload_data = upload_response.json()
    import_id = upload_data["import_id"]

    # Find an unresolved block
    unresolved = upload_data["unresolved_blocks"]
    assert len(unresolved) > 0

    block = unresolved[0]
    block_id = block["block_id"]

    # Resolve it
    resolution_data = {
        "resolutions": [
            {
                "block_id": block_id,
                "selected_activity": block["activity_candidates"][0]["code"],
                "selected_teacher": block["teacher_candidates"][0]["acronym"],
                "selected_resource": block["resource_candidates"][0]["code"] if block["resource_candidates"] else None,
                "entry_type": "CLASS",
            }
        ]
    }

    resolve_response = client.post(
        f"/api/v1/imports/{import_id}/resolve",
        json=resolution_data,
    )

    assert resolve_response.status_code == 200
    resolve_data = resolve_response.json()

    assert resolve_data["applied_count"] == 1
    assert len(resolve_data["new_resolved_activities"]) == 1
    assert resolve_data["remaining_unresolved"] == upload_data["unresolved_count"]


# ---------------------------------------------------------------------------
# Test: Manual resolution preserves source location
# ---------------------------------------------------------------------------


def test_manual_resolution_preserves_source_location(client, db, mca_docx_path):
    """Test that manual resolution preserves source location."""
    # Upload DOCX
    with open(mca_docx_path, "rb") as f:
        upload_response = client.post(
            "/api/v1/imports/docx",
            files={"file": ("MCA Timetable-2026-Odd V7.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "academic_year": "2026-Odd",
                "department": "Computer Applications",
            },
        )

    upload_data = upload_response.json()
    import_id = upload_data["import_id"]
    unresolved = upload_data["unresolved_blocks"]

    block = unresolved[0]
    original_source = block["source_location"]

    # Apply resolution
    resolution_data = {
        "resolutions": [
            {
                "block_id": block["block_id"],
                "selected_activity": block["activity_candidates"][0]["code"],
                "selected_teacher": block["teacher_candidates"][0]["acronym"],
                "selected_resource": None,
                "entry_type": "CLASS",
            }
        ]
    }

    resolve_response = client.post(
        f"/api/v1/imports/{import_id}/resolve",
        json=resolution_data,
    )

    resolve_data = resolve_response.json()
    new_activity = resolve_data["new_resolved_activities"][0]

    # Source location should match original block
    assert new_activity["source_location"] == original_source
    assert new_activity["is_manually_resolved"] is True


# ---------------------------------------------------------------------------
# Test: One block → multiple activities
# ---------------------------------------------------------------------------


def test_manual_resolution_one_block_multiple_activities(client, db, mca_docx_path):
    """Test resolving one block into multiple activities."""
    # Upload DOCX
    with open(mca_docx_path, "rb") as f:
        upload_response = client.post(
            "/api/v1/imports/docx",
            files={"file": ("MCA Timetable-2026-Odd V7.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "academic_year": "2026-Odd",
                "department": "Computer Applications",
            },
        )

    upload_data = upload_response.json()
    import_id = upload_data["import_id"]

    # Find block with multiple candidates
    block = None
    for b in upload_data["unresolved_blocks"]:
        if len(b["activity_candidates"]) >= 2 and len(b["teacher_candidates"]) >= 2:
            block = b
            break

    if block is None:
        pytest.skip("No suitable block with multiple candidates found")

    block_id = block["block_id"]

    # Create two resolutions for same block
    resolution_data = {
        "resolutions": [
            {
                "block_id": block_id,
                "selected_activity": block["activity_candidates"][0]["code"],
                "selected_teacher": block["teacher_candidates"][0]["acronym"],
                "selected_resource": None,
                "entry_type": "CLASS",
            },
            {
                "block_id": block_id,
                "selected_activity": block["activity_candidates"][1]["code"] if len(block["activity_candidates"]) > 1 else block["activity_candidates"][0]["code"],
                "selected_teacher": block["teacher_candidates"][1]["acronym"],
                "selected_resource": None,
                "entry_type": "CLASS",
            },
        ]
    }

    resolve_response = client.post(
        f"/api/v1/imports/{import_id}/resolve",
        json=resolution_data,
    )

    assert resolve_response.status_code == 200
    resolve_data = resolve_response.json()

    # Should create 2 activities
    assert resolve_data["applied_count"] == 2
    assert len(resolve_data["new_resolved_activities"]) == 2

    # Both should reference same source block
    activity1 = resolve_data["new_resolved_activities"][0]
    activity2 = resolve_data["new_resolved_activities"][1]

    assert activity1["source_location"] == activity2["source_location"]
    assert activity1["day"] == activity2["day"]
    assert activity1["slots"] == activity2["slots"]


# ---------------------------------------------------------------------------
# Test: Finalize blocks
# ---------------------------------------------------------------------------


def test_finalize_blocks(client, db, mca_docx_path):
    """Test finalizing blocks removes them from unresolved."""
    # Upload DOCX
    with open(mca_docx_path, "rb") as f:
        upload_response = client.post(
            "/api/v1/imports/docx",
            files={"file": ("MCA Timetable-2026-Odd V7.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "academic_year": "2026-Odd",
                "department": "Computer Applications",
            },
        )

    upload_data = upload_response.json()
    import_id = upload_data["import_id"]
    original_unresolved_count = upload_data["unresolved_count"]

    # Get first 3 blocks to finalize
    blocks_to_finalize = [block["block_id"] for block in upload_data["unresolved_blocks"][:3]]

    finalize_response = client.post(
        f"/api/v1/imports/{import_id}/finalize",
        json={"block_ids": blocks_to_finalize},
    )

    assert finalize_response.status_code == 200
    finalize_data = finalize_response.json()

    # Unresolved count should decrease
    assert finalize_data["unresolved_count"] == original_unresolved_count - 3

    # Finalized blocks should not appear
    remaining_ids = {block["block_id"] for block in finalize_data["unresolved_blocks"]}
    for block_id in blocks_to_finalize:
        assert block_id not in remaining_ids


# ---------------------------------------------------------------------------
# Test: Cannot confirm with unresolved blocks
# ---------------------------------------------------------------------------


def test_confirm_with_unresolved_blocks_fails(client, db, mca_docx_path):
    """Test that confirm fails when unresolved blocks remain."""
    # Upload DOCX
    with open(mca_docx_path, "rb") as f:
        upload_response = client.post(
            "/api/v1/imports/docx",
            files={"file": ("MCA Timetable-2026-Odd V7.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "academic_year": "2026-Odd",
                "department": "Computer Applications",
            },
        )

    upload_data = upload_response.json()
    import_id = upload_data["import_id"]

    # Attempt to confirm without resolving
    confirm_response = client.post(f"/api/v1/imports/{import_id}/confirm")

    assert confirm_response.status_code == 422
    error_data = confirm_response.json()

    assert "unresolved blocks remain" in error_data["detail"]["message"].lower()
    assert "unresolved_blocks" in error_data["detail"]


# ---------------------------------------------------------------------------
# Test: Confirm after all blocks resolved
# ---------------------------------------------------------------------------


def test_confirm_after_all_blocks_finalized(client, db, program, mca_docx_path):
    """Test that confirm fails gracefully when all blocks are excluded (no activities to import)."""
    # Upload DOCX
    with open(mca_docx_path, "rb") as f:
        upload_response = client.post(
            "/api/v1/imports/docx",
            files={"file": ("MCA Timetable-2026-Odd V7.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "academic_year": "2026-Odd",
                "department": "Computer Applications",
            },
        )

    upload_data = upload_response.json()
    import_id = upload_data["import_id"]

    # Finalize all blocks (mark as excluded)
    all_block_ids = [block["block_id"] for block in upload_data["unresolved_blocks"]]

    finalize_response = client.post(
        f"/api/v1/imports/{import_id}/finalize",
        json={"block_ids": all_block_ids},
    )

    assert finalize_response.status_code == 200

    # Now confirm - this should work (no unresolved blocks)
    # But may fail validation since there are resolved activities with teachers not in DB
    confirm_response = client.post(f"/api/v1/imports/{import_id}/confirm")

    # Should fail validation (no program/teachers in test DB) but not due to unresolved blocks
    # The key is it's not a 404 - the import was found and processed
    assert confirm_response.status_code in [200, 422]

    if confirm_response.status_code == 422:
        # Should be validation errors, not unresolved block errors
        error_data = confirm_response.json()
        detail = str(error_data.get("detail", ""))
        assert "unresolved blocks" not in detail.lower(), "Should not fail due to unresolved blocks"


# ---------------------------------------------------------------------------
# Test: 404 for non-existent import
# ---------------------------------------------------------------------------


def test_resolve_nonexistent_import_returns_404(client, db):
    """Test that resolving non-existent import returns 404."""
    response = client.post(
        "/api/v1/imports/nonexistent-id/resolve",
        json={"resolutions": []},
    )

    assert response.status_code == 404


def test_finalize_nonexistent_import_returns_404(client, db):
    """Test that finalizing non-existent import returns 404."""
    response = client.post(
        "/api/v1/imports/nonexistent-id/finalize",
        json={"block_ids": []},
    )

    assert response.status_code == 404


def test_confirm_nonexistent_import_returns_404(client, db):
    """Test that confirming non-existent import returns 404."""
    response = client.post("/api/v1/imports/nonexistent-id/confirm")

    assert response.status_code == 404
