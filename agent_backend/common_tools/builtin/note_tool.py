from datetime import datetime


class NoteTool:
    def __init__(self):
        self.notes = {}

    def write(self, note_id: str, title: str, content: str, tags: list[str] | None = None):
        self.notes[note_id] = {
            "title": title,
            "content": content,
            "tags": tags or [],
            "updated_at": datetime.utcnow().isoformat(),
        }
        return self.notes[note_id]

    def read(self, note_id: str):
        return self.notes.get(note_id)

    def search(self, keyword: str):
        out = []
        for k, v in self.notes.items():
            if keyword in v["title"] or keyword in v["content"]:
                out.append({"id": k, **v})
        return out
