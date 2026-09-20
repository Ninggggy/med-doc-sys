class Neo4jStore:
    """In-memory graph placeholder implementation."""

    def __init__(self):
        self.nodes = {}
        self.edges = []

    def add_node(self, node_id: str, props: dict):
        self.nodes[node_id] = props

    def add_edge(self, src: str, rel: str, dst: str):
        self.edges.append((src, rel, dst))

    def query_node(self, node_id: str):
        return self.nodes.get(node_id)
