"""Script to regenerate catalog and docs vector databases under src/grc_agent/vectors/."""

import os
from pathlib import Path

from grc_agent.retrieval import warmup_catalog_vector_index
from grc_agent.runtime.doc_answer import DB_PATH, VectorDocsStore


def main():
    vectors_dir = Path(__file__).resolve().parents[1] / "src" / "grc_agent" / "vectors"
    vectors_dir.mkdir(parents=True, exist_ok=True)

    print(f"Target vectors directory: {vectors_dir}")

    # Remove existing db files if any
    for f in vectors_dir.glob("*.db*"):
        print(f"Removing old vector file: {f.name}")
        f.unlink()

    server_url = os.environ.get("GRC_AGENT_LLAMA_SERVER_URL", "http://localhost:11434")
    print(f"Using Llama/Ollama server URL: {server_url}")

    print("Ingesting catalog vector index...")
    res = warmup_catalog_vector_index(server_url=server_url)
    print(f"Catalog vector index result: {res}")

    print("Ingesting docs vector index...")
    store = VectorDocsStore(DB_PATH, server_url)
    res_docs = store.ingest_if_needed()
    print(f"Docs vector index ingested {res_docs} chunks.")

    print("\nVerification:")
    from grc_agent.runtime.catalog_vector import CATALOG_DB_PATH, is_catalog_db_usable
    from grc_agent.runtime.doc_answer import is_docs_db_usable

    print(f"Catalog DB usable: {is_catalog_db_usable(CATALOG_DB_PATH)}")
    print(f"Docs DB usable: {is_docs_db_usable(DB_PATH)}")


if __name__ == "__main__":
    main()
