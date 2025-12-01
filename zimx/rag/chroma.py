from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from chromadb import PersistentClient
from chromadb.config import Settings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from zimx.rag import telemetry
from zimx.rag.index import RetrievedChunk


@dataclass
class ChromaRAG:
    vault_root: str
    collection_name: str = "vault-context"

    def __post_init__(self) -> None:
        base = Path(self.vault_root) / ".zimx" / "chroma"
        base.mkdir(parents=True, exist_ok=True)
        self.persist_dir = str(base)
        telemetry_impl = f"{telemetry.NoopTelemetryClient.__module__}.{telemetry.NoopTelemetryClient.__name__}"
        settings = Settings(
            chroma_api_impl="chromadb.api.rust.RustBindingsAPI",
            is_persistent=True,
            persist_directory=self.persist_dir,
            allow_reset=True,
            chroma_product_telemetry_impl=telemetry_impl,
            chroma_telemetry_impl=telemetry_impl,
            anonymized_telemetry=False,
        )
        self.client = PersistentClient(
            path=self.persist_dir,
            settings=settings,
        )
        self.collection = self._ensure_collection()

    def query(
        self,
        query_text: str,
        page_refs: Optional[list[str]] = None,
        limit: int = 4,
    ) -> list[RetrievedChunk]:
        if not query_text.strip():
            return []
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
                where={"page_ref": {"$in": page_refs}} if page_refs else None,
            )
        except Exception as exc:
            print(f"[Chroma] Query failed: {exc}")
            return []
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        chunks: list[RetrievedChunk] = []
        for idx, doc in enumerate(documents):
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            distance = distances[idx] if idx < len(distances) else None
            chunk = RetrievedChunk(
                page_ref=metadata.get("page_ref", ""),
                content=doc or "",
                score=distance,
                attachment_name=metadata.get("attachment_name"),
            )
            chunks.append(chunk)
        return chunks

    def query_attachments(self, query_text: str, attachment_names: list[str], limit: int = 4) -> list[RetrievedChunk]:
        if not query_text.strip() or not attachment_names:
            return []
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
                where={"attachment_name": {"$in": attachment_names}},
            )
        except Exception as exc:
            print(f"[Chroma] Attachment query failed: {exc}")
            return []
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        chunks: list[RetrievedChunk] = []
        for idx, doc in enumerate(documents):
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            distance = distances[idx] if idx < len(distances) else None
            chunk = RetrievedChunk(
                page_ref=metadata.get("page_ref", ""),
                content=doc or "",
                score=distance,
                attachment_name=metadata.get("attachment_name"),
            )
            chunks.append(chunk)
        return chunks
    def _ensure_collection(self):
        if self.collection_name in [c.name for c in self.client.list_collections()]:
            return self.client.get_collection(name=self.collection_name)
        return self.client.create_collection(
            name=self.collection_name,
            embedding_function=DefaultEmbeddingFunction(),
        )

    def _doc_id(self, page_ref: str, kind: str, attachment: Optional[str] = None) -> str:
        if kind == "attachment" and attachment:
            return f"{page_ref}:{attachment}"
        return f"{page_ref}:{kind}"

    def index_text(self, page_ref: str, text: str, kind: str, attachment: Optional[str] = None) -> None:
        if not text.strip():
            print(f"[Chroma] Skipping empty text for {page_ref}")
            return
        doc_id = self._doc_id(page_ref, kind, attachment)
        metadata = {"page_ref": page_ref, "kind": kind}
        if attachment:
            metadata["attachment_name"] = attachment
        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )
        print(f"[Chroma] Indexed {kind} context {doc_id}")

    def delete_text(self, page_ref: str, kind: str, attachment: Optional[str] = None) -> None:
        doc_id = self._doc_id(page_ref, kind, attachment)
        try:
            self.collection.delete(ids=[doc_id])
            print(f"[Chroma] Delete request for {doc_id}")
        except Exception as exc:
            print(f"[Chroma] Failed to delete {doc_id}: {exc}")

    def close(self) -> None:
        self.client.persist()
