from dji_recon.storage import _parse_mega_paths


def test_parse_mega_paths_handles_spaces_control_codes_and_duplicates():
    output = "MEGA CMD>\x00\r\n/Attempt 1/DJI_0002.DNG\n/Attempt 1/DJI_0001.dng\n/Attempt 1/DJI_0002.DNG\n/readme.txt\n"
    assert _parse_mega_paths(output) == ["/Attempt 1/DJI_0001.dng", "/Attempt 1/DJI_0002.DNG"]
