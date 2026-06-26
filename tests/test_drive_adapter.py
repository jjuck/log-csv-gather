from __future__ import annotations

from log_csv_gather.drive import GoogleDriveAdapter


class FakeRequest:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.execute_kwargs: list[dict] = []

    def execute(self, **kwargs):
        self.execute_kwargs.append(kwargs)
        return self.response


class FakeFilesResource:
    def __init__(self) -> None:
        self.last_list_request: FakeRequest | None = None

    def list(self, **kwargs):
        self.last_list_request = FakeRequest({"files": []})
        return self.last_list_request


class FakeService:
    def __init__(self) -> None:
        self.files_resource = FakeFilesResource()

    def files(self):
        return self.files_resource


def test_find_file_executes_drive_request_with_configured_retries() -> None:
    service = FakeService()
    adapter = GoogleDriveAdapter(service, "root-folder-id", num_retries=5)

    assert adapter.find_file("logs/file.csv") is None

    request = service.files_resource.last_list_request
    assert request is not None
    assert request.execute_kwargs == [{"num_retries": 5}]
