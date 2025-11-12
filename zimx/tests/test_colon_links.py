"""Test cases for colon notation path conversion."""
from __future__ import annotations

import pytest

from zimx.app.ui.path_utils import path_to_colon, colon_to_path, colon_to_folder_path


class TestPathToColon:
    """Test converting filesystem paths to colon notation."""
    
    def test_simple_single_level(self):
        """Single level page: /PageA/PageA.txt -> PageA"""
        result = path_to_colon("/PageA/PageA.txt")
        assert result == "PageA"
    
    def test_two_level_hierarchy(self):
        """Two level page: /PageA/PageB/PageB.txt -> PageA:PageB"""
        result = path_to_colon("/PageA/PageB/PageB.txt")
        assert result == "PageA:PageB"
    
    def test_three_level_hierarchy(self):
        """Three level page: /PageA/PageB/PageC/PageC.txt -> PageA:PageB:PageC"""
        result = path_to_colon("/PageA/PageB/PageC/PageC.txt")
        assert result == "PageA:PageB:PageC"
    
    def test_joebob_example(self):
        """Real world example: /JoeBob/JoeBob2/JoeBob2.txt -> JoeBob:JoeBob2"""
        result = path_to_colon("/JoeBob/JoeBob2/JoeBob2.txt")
        assert result == "JoeBob:JoeBob2"
    
    def test_without_leading_slash(self):
        """Path without leading slash should still work"""
        result = path_to_colon("PageA/PageB/PageB.txt")
        assert result == "PageA:PageB"
    
    def test_with_trailing_slash(self):
        """Path with trailing slashes should be cleaned"""
        result = path_to_colon("/PageA/PageB/PageB.txt/")
        assert result == "PageA:PageB"
    
    def test_empty_path(self):
        """Empty path should return empty string"""
        result = path_to_colon("")
        assert result == ""
    
    def test_root_path(self):
        """Root path should return empty string"""
        result = path_to_colon("/")
        assert result == ""
    
    def test_deep_hierarchy(self):
        """Deep nested structure"""
        result = path_to_colon("/A/B/C/D/E/F/F.txt")
        assert result == "A:B:C:D:E:F"
    
    def test_numeric_names(self):
        """Pages with numeric names"""
        result = path_to_colon("/2025/11/12/12.txt")
        assert result == "2025:11:12"
    
    def test_names_with_underscores(self):
        """Pages with underscores in names"""
        result = path_to_colon("/My_Page/Sub_Page/Sub_Page.txt")
        assert result == "My_Page:Sub_Page"
    
    def test_mixed_case_names(self):
        """Pages with mixed case names"""
        result = path_to_colon("/MyPage/SubPage/SubPage.txt")
        assert result == "MyPage:SubPage"


class TestColonToPath:
    """Test converting colon notation to filesystem paths."""
    
    def test_simple_single_level(self):
        """PageA -> /PageA/PageA.txt"""
        result = colon_to_path("PageA")
        assert result == "/PageA/PageA.txt"
    
    def test_two_level_hierarchy(self):
        """PageA:PageB -> /PageA/PageB/PageB.txt"""
        result = colon_to_path("PageA:PageB")
        assert result == "/PageA/PageB/PageB.txt"
    
    def test_three_level_hierarchy(self):
        """PageA:PageB:PageC -> /PageA/PageB/PageC/PageC.txt"""
        result = colon_to_path("PageA:PageB:PageC")
        assert result == "/PageA/PageB/PageC/PageC.txt"
    
    def test_joebob_example(self):
        """JoeBob:JoeBob2 -> /JoeBob/JoeBob2/JoeBob2.txt"""
        result = colon_to_path("JoeBob:JoeBob2")
        assert result == "/JoeBob/JoeBob2/JoeBob2.txt"
    
    def test_empty_path(self):
        """Empty colon path should return /"""
        result = colon_to_path("")
        assert result == "/"
    
    def test_empty_path_with_vault_root(self):
        """Empty path with vault root should return vault root file"""
        result = colon_to_path("", vault_root_name="MyVault")
        assert result == "/MyVault.txt"
    
    def test_deep_hierarchy(self):
        """Deep nested structure"""
        result = colon_to_path("A:B:C:D:E:F")
        assert result == "/A/B/C/D/E/F/F.txt"
    
    def test_numeric_names(self):
        """Pages with numeric names"""
        result = colon_to_path("2025:11:12")
        assert result == "/2025/11/12/12.txt"
    
    def test_names_with_underscores(self):
        """Pages with underscores"""
        result = colon_to_path("My_Page:Sub_Page")
        assert result == "/My_Page/Sub_Page/Sub_Page.txt"


class TestColonToFolderPath:
    """Test converting colon notation to folder paths (without .txt file)."""
    
    def test_simple_single_level(self):
        """PageA -> /PageA"""
        result = colon_to_folder_path("PageA")
        assert result == "/PageA"
    
    def test_two_level_hierarchy(self):
        """PageA:PageB -> /PageA/PageB"""
        result = colon_to_folder_path("PageA:PageB")
        assert result == "/PageA/PageB"
    
    def test_three_level_hierarchy(self):
        """PageA:PageB:PageC -> /PageA/PageB/PageC"""
        result = colon_to_folder_path("PageA:PageB:PageC")
        assert result == "/PageA/PageB/PageC"
    
    def test_empty_path(self):
        """Empty path should return /"""
        result = colon_to_folder_path("")
        assert result == "/"


class TestRoundTrip:
    """Test that conversions are reversible."""
    
    def test_path_to_colon_to_path(self):
        """Converting path -> colon -> path should return original"""
        original = "/PageA/PageB/PageC/PageC.txt"
        colon = path_to_colon(original)
        back = colon_to_path(colon)
        assert back == original
    
    def test_colon_to_path_to_colon(self):
        """Converting colon -> path -> colon should return original"""
        original = "PageA:PageB:PageC"
        path = colon_to_path(original)
        back = path_to_colon(path)
        assert back == original
    
    def test_multiple_round_trips(self):
        """Multiple conversions should be stable"""
        test_cases = [
            "/A/A.txt",
            "/A/B/B.txt",
            "/A/B/C/C.txt",
            "/JoeBob/JoeBob2/JoeBob2.txt",
            "/Journal/2025/11/11.txt",
        ]
        for original in test_cases:
            colon = path_to_colon(original)
            back = colon_to_path(colon)
            assert back == original, f"Round trip failed for {original}: got {back}"
    
    def test_joebob_round_trip(self):
        """Specific test for the JoeBob issue"""
        # Start with filesystem path
        file_path = "/JoeBob/JoeBob2/JoeBob2.txt"
        
        # Convert to colon notation
        colon = path_to_colon(file_path)
        assert colon == "JoeBob:JoeBob2"
        
        # Convert back to filesystem path
        back = colon_to_path(colon)
        assert back == "/JoeBob/JoeBob2/JoeBob2.txt"
        
        # Ensure no duplicate folders
        assert back.count("JoeBob2") == 2  # Once in folder, once in filename
        assert "/JoeBob2/JoeBob2/" not in back  # No duplicate folder structure


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_single_character_names(self):
        """Single character page names"""
        assert path_to_colon("/A/B/B.txt") == "A:B"
        assert colon_to_path("A:B") == "/A/B/B.txt"
    
    def test_names_with_numbers(self):
        """Names mixing letters and numbers"""
        assert path_to_colon("/Page1/Page2/Page2.txt") == "Page1:Page2"
        assert colon_to_path("Page1:Page2") == "/Page1/Page2/Page2.txt"
    
    def test_whitespace_handling(self):
        """Paths with extra whitespace should be handled"""
        result = path_to_colon("  /PageA/PageB/PageB.txt  ")
        assert result == "PageA:PageB"
    
    def test_vault_root_special_case(self):
        """Vault root name is used for empty colon path"""
        result = colon_to_path("", vault_root_name="TestVault")
        assert result == "/TestVault.txt"
    
    def test_very_long_hierarchy(self):
        """Very deep nesting should work"""
        parts = [f"Level{i}" for i in range(10)]
        file_path = "/" + "/".join(parts) + f"/{parts[-1]}.txt"
        colon = path_to_colon(file_path)
        back = colon_to_path(colon)
        assert back == file_path
