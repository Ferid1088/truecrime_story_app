from pathlib import Path

from config_read_recorder import install


class Section:
    model_fields = {"threshold": object()}

    def __init__(self):
        self.threshold = 3

    def model_dump(self):
        return {"threshold": self.threshold}


class Config:
    model_fields = {"section": object()}

    def __init__(self):
        self.section = Section()


def test_proxy_records_nested_reads_and_restores(tmp_path: Path):
    config = Config()
    reads, restore = install(config, tmp_path / "reads.json")
    assert config.section.threshold == 3
    assert "section.threshold" in reads
    restore()
    assert config.section.threshold == 3
    assert "section.threshold" in (tmp_path / "reads.json").read_text()
