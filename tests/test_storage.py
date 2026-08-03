from dji_recon.storage import _mega_export_relative, _parse_mega_paths


def test_parse_mega_paths_handles_spaces_control_codes_and_duplicates():
    output = "MEGA CMD>\x00\r\n/Attempt 1/DJI_0002.DNG\n/Attempt 1/DJI_0001.dng\n/Attempt 1/DJI_0002.DNG\n/readme.txt\n"
    assert _parse_mega_paths(output) == ["/Attempt 1/DJI_0001.dng", "/Attempt 1/DJI_0002.DNG"]


def test_exported_folder_paths_are_relative_to_share_root():
    assert _mega_export_relative("/Attempt 1/DJI_0001.DNG") == "DJI_0001.DNG"
    assert _mega_export_relative("/Attempt 1/nested/DJI_0002.DNG") == "nested/DJI_0002.DNG"
