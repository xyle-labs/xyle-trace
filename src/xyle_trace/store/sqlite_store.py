import json
import pathlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType, Record, Resolution
from xyle_trace.store.schema import SCHEMA, SCHEMA_VERSION


class SchemaVersionError(ValueError):
    """The database cannot safely be opened by this version of the library."""


class Store:
    def __init__(self, path: pathlib.Path, *, create: bool = True) -> None:
        self.path = pathlib.Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = (
            sqlite3.connect(self.path)
            if create
            else sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True)
        )
        self._conn.row_factory = sqlite3.Row
        self._savepoint_sequence = 0
        try:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise SchemaVersionError(f"unsupported database schema version {version}")
            expected = {
                "nodes": "project_id id type natural_key data created_at",
                "edges": "project_id id type src dst data created_at",
                "records": "project_id dataset_id key data",
                "resolutions": "project_id dataset_id record_key status target_id created_at",
            }
            tables = {
                r[0]
                for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if (tables or version or not create) and (
                tables != set(expected)
                or any(
                    [r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")]
                    != columns.split()
                    for table, columns in expected.items()
                )
            ):
                raise SchemaVersionError("database does not match the lineage schema")
            self._conn.executescript(SCHEMA)
            if version == 0:
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()
        except Exception:
            self._conn.close()
            raise

    def close(self) -> None:
        self._conn.close()

    @property
    def in_transaction(self) -> bool:
        return self._conn.in_transaction

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[None]:
        """Group writes atomically; nested operations roll back to their own savepoint.

        Hold this context only around database work, never network transfers or
        computations. Revision checks must happen inside the same context as writes.
        Use write=False for a consistent read snapshot without reserving a writer.
        """
        if self._conn.in_transaction:
            self._savepoint_sequence += 1
            savepoint = f"lineage_{self._savepoint_sequence}"
            self._conn.execute(f"SAVEPOINT {savepoint}")
            try:
                yield
                self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
        else:
            self._conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def upsert_node(self, node: Node) -> Node:
        with self.transaction():
            previous = self.get_node(node.project_id, node.id)
            data = json.dumps(node.data, sort_keys=True, allow_nan=False)
            if (
                previous is not None
                and json.dumps(previous.data, sort_keys=True, allow_nan=False) != data
            ):
                if node.type == NodeType.SOURCE_SNAPSHOT:
                    raise ValueError("source snapshots are immutable; register a new version")
                self._review_downstream(node.project_id, node.id)
                self._conn.execute(
                    "DELETE FROM resolutions WHERE project_id = ? AND target_id = ?",
                    (node.project_id, node.id),
                )
            self._conn.execute(
                """INSERT INTO nodes (project_id, id, type, natural_key, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (project_id, id) DO UPDATE SET data = excluded.data""",
                (
                    node.project_id,
                    node.id,
                    node.type.value,
                    node.natural_key,
                    data,
                    node.created_at.isoformat(),
                ),
            )
        return self.get_node(node.project_id, node.id)

    def get_node(self, project_id: str, node_id: str) -> Node | None:
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE project_id = ? AND id = ?", (project_id, node_id)
        ).fetchone()
        return self._row_to_node(row) if row else None

    def dataset_node(self, project_id: str, selector: str) -> Node | None:
        """Resolve a dataset ID or natural key within one project."""
        rows = self._conn.execute(
            """SELECT * FROM nodes WHERE project_id = ? AND type = ?
               AND (id = ? OR natural_key = ?)""",
            (project_id, NodeType.DATASET.value, selector, selector),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError(f"ambiguous dataset reference {selector!r}")
        return self._row_to_node(rows[0]) if rows else None

    def _dataset_id(self, project_id: str, selector: str, *, required: bool = False) -> str:
        node = self.dataset_node(project_id, selector)
        if node is None and required:
            raise ValueError(f"dataset {selector!r} not registered in project {project_id!r}")
        return node.id if node else selector

    def nodes_of_type(self, project_id: str, node_type: NodeType) -> list[Node]:
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE project_id = ? AND type = ? ORDER BY natural_key",
            (project_id, node_type.value),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def upsert_edge(self, edge: Edge) -> Edge:
        with self.transaction():
            self._conn.execute(
                """INSERT INTO edges (project_id, id, type, src, dst, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (project_id, id) DO UPDATE SET data = excluded.data""",
                (
                    edge.project_id,
                    edge.id,
                    edge.type.value,
                    edge.src,
                    edge.dst,
                    json.dumps(edge.data),
                    edge.created_at.isoformat(),
                ),
            )
        return edge

    def edges_from(
        self, project_id: str, node_id: str, types: list[EdgeType] | None = None
    ) -> list[Edge]:
        return self._edges("src", project_id, node_id, types)

    def get_edge(self, project_id: str, edge_id: str) -> Edge | None:
        row = self._conn.execute(
            "SELECT * FROM edges WHERE project_id = ? AND id = ?", (project_id, edge_id)
        ).fetchone()
        return self._row_to_edge(row) if row else None

    def edges_to(
        self, project_id: str, node_id: str, types: list[EdgeType] | None = None
    ) -> list[Edge]:
        return self._edges("dst", project_id, node_id, types)

    def _edges(
        self, column: str, project_id: str, node_id: str, types: list[EdgeType] | None
    ) -> list[Edge]:
        sql = f"SELECT * FROM edges WHERE project_id = ? AND {column} = ?"
        params: list[str] = [project_id, node_id]
        if types:
            sql += f" AND type IN ({','.join('?' * len(types))})"
            params.extend(t.value for t in types)
        rows = self._conn.execute(sql + " ORDER BY id", params).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def put_records(self, records: list[Record]) -> int:
        with self.transaction():
            reviewed = set()
            for record in records:
                dataset_id = self._dataset_id(record.project_id, record.dataset_id, required=True)
                key = (record.project_id, dataset_id, record.key)
                data = json.dumps(record.data, sort_keys=True, allow_nan=False)
                previous = self._conn.execute(
                    "SELECT data FROM records WHERE project_id = ? AND dataset_id = ? AND key = ?",
                    key,
                ).fetchone()
                if (
                    previous is None
                    or json.dumps(json.loads(previous["data"]), sort_keys=True, allow_nan=False)
                    != data
                ):
                    scope = (record.project_id, dataset_id)
                    if scope not in reviewed:
                        self._review_downstream(*scope)
                        reviewed.add(scope)
                    self._conn.execute(
                        """DELETE FROM resolutions
                           WHERE project_id = ? AND dataset_id = ? AND record_key = ?""",
                        key,
                    )
                self._conn.execute(
                    """INSERT INTO records (project_id, dataset_id, key, data)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (project_id, dataset_id, key)
                       DO UPDATE SET data = excluded.data""",
                    (*key, data),
                )
        return len(records)

    def records(self, project_id: str, dataset_id: str, *, resolve: bool = True) -> list[Record]:
        if resolve:
            dataset_id = self._dataset_id(project_id, dataset_id)
        rows = self._conn.execute(
            "SELECT * FROM records WHERE project_id = ? AND dataset_id = ? ORDER BY key",
            (project_id, dataset_id),
        ).fetchall()
        return [
            Record(
                project_id=r["project_id"],
                dataset_id=r["dataset_id"],
                key=r["key"],
                data=json.loads(r["data"]),
            )
            for r in rows
        ]

    def put_resolution(self, resolution: Resolution) -> Resolution:
        with self.transaction():
            dataset_id = self._dataset_id(
                resolution.project_id, resolution.dataset_id, required=True
            )
            resolution = resolution.model_copy(update={"dataset_id": dataset_id})
            exists = self._conn.execute(
                "SELECT 1 FROM records WHERE project_id = ? AND dataset_id = ? AND key = ?",
                (resolution.project_id, dataset_id, resolution.record_key),
            ).fetchone()
            if not exists:
                raise ValueError(f"record {resolution.record_key!r} does not exist")
            previous = self.resolutions(resolution.project_id, dataset_id).get(
                resolution.record_key
            )
            if previous and (previous.status, previous.target_id) == (
                resolution.status,
                resolution.target_id,
            ):
                return previous
            self._review_downstream(resolution.project_id, dataset_id)
            self._conn.execute(
                """INSERT INTO resolutions
                   (project_id, dataset_id, record_key, status, target_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (project_id, dataset_id, record_key) DO UPDATE SET
                   status = excluded.status,
                   target_id = excluded.target_id,
                   created_at = excluded.created_at""",
                (
                    resolution.project_id,
                    resolution.dataset_id,
                    resolution.record_key,
                    resolution.status,
                    resolution.target_id,
                    resolution.created_at.isoformat(),
                ),
            )
        return resolution

    def resolutions(
        self, project_id: str, dataset_id: str, *, resolve: bool = True
    ) -> dict[str, Resolution]:
        if resolve:
            dataset_id = self._dataset_id(project_id, dataset_id)
        rows = self._conn.execute(
            """SELECT * FROM resolutions WHERE project_id = ? AND dataset_id = ?
               ORDER BY record_key""",
            (project_id, dataset_id),
        ).fetchall()
        return {
            r["record_key"]: Resolution(
                project_id=r["project_id"],
                dataset_id=r["dataset_id"],
                record_key=r["record_key"],
                status=r["status"],
                target_id=r["target_id"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        }

    def unresolved_keys(self, project_id: str, dataset_id: str) -> list[str]:
        dataset_id = self._dataset_id(project_id, dataset_id)
        rows = self._conn.execute(
            """SELECT r.key FROM records r
               LEFT JOIN resolutions s
                 ON  s.project_id = r.project_id
                 AND s.dataset_id = r.dataset_id
                 AND s.record_key = r.key
               WHERE r.project_id = ? AND r.dataset_id = ? AND s.record_key IS NULL
               ORDER BY r.key""",
            (project_id, dataset_id),
        ).fetchall()
        return [r["key"] for r in rows]

    def resolution_dependencies(self, project_id: str) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            """SELECT DISTINCT s.dataset_id, s.target_id FROM resolutions s
               JOIN records r ON r.project_id = s.project_id AND r.dataset_id = s.dataset_id
                 AND r.key = s.record_key
               JOIN nodes d ON d.project_id = s.project_id AND d.id = s.dataset_id
                 AND d.type = 'dataset'
               JOIN nodes t ON t.project_id = s.project_id AND t.id = s.target_id
               WHERE s.project_id = ? ORDER BY s.dataset_id, s.target_id""",
            (project_id,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def _review_downstream(self, project_id: str, node_id: str) -> None:
        # Imported here because traversal uses Store. The caller owns the transaction.
        from xyle_trace.queries.traversal import impact

        self.mark_claims_for_review(
            project_id, impact(self, project_id, node_id)["claims_needing_review"]
        )

    def mark_claims_for_review(self, project_id: str, claim_ids: list[str]) -> None:
        with self.transaction():
            for claim_id in claim_ids:
                claim = self.get_node(project_id, claim_id)
                if claim is None or claim.type != NodeType.CLAIM:
                    raise ValueError(f"claim {claim_id!r} does not exist in project")
                self._conn.execute(
                    "UPDATE nodes SET data = ? WHERE project_id = ? AND id = ?",
                    (
                        json.dumps({**claim.data, "review_status": "needs_review"}),
                        project_id,
                        claim_id,
                    ),
                )

    def review_claim(self, project_id: str, claim_id: str, decision_id: str, status: str) -> Node:
        """Record review state without treating it as a change to the claim's content.

        The application service checks revisions and records the review edge.
        """
        with self.transaction():
            claim = self.get_node(project_id, claim_id)
            decision = self.get_node(project_id, decision_id)
            if claim is None or claim.type != NodeType.CLAIM:
                raise ValueError("review subject must be a claim in this project")
            if decision is None or decision.type != NodeType.DECISION:
                raise ValueError("review must reference a decision in this project")
            if status not in {"reviewed", "needs_review"}:
                raise ValueError("invalid claim review status")
            data = {**claim.data, "review_status": status, "review_decision_id": decision_id}
            self._conn.execute(
                "UPDATE nodes SET data = ? WHERE project_id = ? AND id = ?",
                (json.dumps(data), project_id, claim_id),
            )
            return self.get_node(project_id, claim_id)

    def all_nodes(self, project_id: str) -> list[Node]:
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def all_edges(self, project_id: str) -> list[Edge]:
        rows = self._conn.execute(
            "SELECT * FROM edges WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def dataset_ids(self, project_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT dataset_id FROM records WHERE project_id = ? ORDER BY dataset_id",
            (project_id,),
        ).fetchall()
        return [r["dataset_id"] for r in rows]

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> Node:
        return Node(
            project_id=row["project_id"],
            type=NodeType(row["type"]),
            natural_key=row["natural_key"],
            data=json.loads(row["data"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> Edge:
        return Edge(
            project_id=row["project_id"],
            type=EdgeType(row["type"]),
            src=row["src"],
            dst=row["dst"],
            data=json.loads(row["data"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
