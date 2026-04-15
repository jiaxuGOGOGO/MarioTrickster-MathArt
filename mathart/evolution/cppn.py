"""
CPPN (Compositional Pattern Producing Network) for procedural texture generation.

CPPNs are neural networks that map (x, y) coordinates to color values,
producing infinitely zoomable, resolution-independent patterns. When evolved
with NEAT or simple mutation, they generate diverse organic textures.

This implementation is self-contained (no external NEAT library needed)
and uses NumPy for fast batch evaluation.

Mathematical foundation:
  - A CPPN is a directed acyclic graph of activation functions
  - Input: (x, y, d, bias) where d = sqrt(x^2 + y^2)
  - Each node applies: output = activation(sum(w_i * input_i) + bias)
  - Activation functions: sin, cos, tanh, sigmoid, gaussian, abs, identity, step
  - Output: (r, g, b) or (value,) for grayscale

References:
  - Stanley, K.O. (2007). "Compositional pattern producing networks:
    A novel abstraction of development." Genetic Programming and Evolvable Machines.
  - Secretan et al. (2011). "Picbreeder: A Case Study in Collaborative
    Evolutionary Exploration of Design Space." Evolutionary Computation.
"""
from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from PIL import Image


# ── Activation Functions ──────────────────────────────────────────────────────

def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -10, 10)))

def _gaussian(x: np.ndarray) -> np.ndarray:
    return np.exp(-x * x * 2.0)

def _step(x: np.ndarray) -> np.ndarray:
    return np.where(x > 0, 1.0, 0.0)

def _sawtooth(x: np.ndarray) -> np.ndarray:
    return 2.0 * (x / (2 * np.pi) - np.floor(x / (2 * np.pi) + 0.5))

def _spike(x: np.ndarray) -> np.ndarray:
    """Spike function: sharp peak at 0."""
    return 1.0 / (1.0 + np.abs(x) * 5.0)

ACTIVATIONS: dict[str, Callable] = {
    "sin": np.sin,
    "cos": np.cos,
    "tanh": np.tanh,
    "sigmoid": _sigmoid,
    "gaussian": _gaussian,
    "abs": np.abs,
    "identity": lambda x: x,
    "step": _step,
    "sawtooth": _sawtooth,
    "spike": _spike,
}

ACTIVATION_NAMES = list(ACTIVATIONS.keys())


# ── CPPN Node & Network ──────────────────────────────────────────────────────

@dataclass
class CPPNNode:
    """A single node in the CPPN graph."""
    node_id: int
    activation: str = "identity"
    bias: float = 0.0

    def activate(self, x: np.ndarray) -> np.ndarray:
        fn = ACTIVATIONS.get(self.activation, ACTIVATIONS["identity"])
        return fn(x + self.bias)


@dataclass
class CPPNConnection:
    """A weighted connection between two nodes."""
    src: int
    dst: int
    weight: float = 1.0
    enabled: bool = True


@dataclass
class CPPNGenome:
    """A CPPN genome that can be mutated and evaluated.

    Default topology: 4 inputs → 3 outputs (RGB) or 1 output (grayscale).
    Inputs: x, y, d (distance from center), bias (always 1.0)
    """
    nodes: list[CPPNNode] = field(default_factory=list)
    connections: list[CPPNConnection] = field(default_factory=list)
    n_inputs: int = 4   # x, y, d, bias
    n_outputs: int = 3  # r, g, b
    fitness: float = 0.0
    _next_node_id: int = 0

    @classmethod
    def create_minimal(cls, n_outputs: int = 3, seed: Optional[int] = None) -> "CPPNGenome":
        """Create a minimal CPPN with direct input→output connections."""
        rng = random.Random(seed)
        genome = cls(n_outputs=n_outputs)

        # Input nodes (0-3): x, y, d, bias
        for i in range(4):
            genome.nodes.append(CPPNNode(node_id=i, activation="identity"))

        # Output nodes (4, 5, 6 for RGB)
        for i in range(n_outputs):
            act = rng.choice(["sigmoid", "tanh", "gaussian"])
            genome.nodes.append(CPPNNode(node_id=4 + i, activation=act, bias=rng.gauss(0, 0.5)))

        genome._next_node_id = 4 + n_outputs

        # Connect each input to each output with random weights
        for inp in range(4):
            for out in range(4, 4 + n_outputs):
                w = rng.gauss(0, 1.0)
                genome.connections.append(CPPNConnection(src=inp, dst=out, weight=w))

        return genome

    def copy(self) -> "CPPNGenome":
        return copy.deepcopy(self)

    def mutate(self, rng: Optional[random.Random] = None) -> "CPPNGenome":
        """Mutate the genome with various operators.

        Mutation types:
          - Weight perturbation (80% chance)
          - Bias perturbation (40% chance)
          - Activation change (20% chance)
          - Add connection (15% chance)
          - Add node (10% chance)
          - Toggle connection (10% chance)
        """
        rng = rng or random.Random()
        child = self.copy()

        # Weight perturbation
        if rng.random() < 0.8 and child.connections:
            conn = rng.choice(child.connections)
            if rng.random() < 0.1:
                conn.weight = rng.gauss(0, 2.0)  # Reset
            else:
                conn.weight += rng.gauss(0, 0.3)  # Perturb
            conn.weight = max(-5.0, min(5.0, conn.weight))

        # Bias perturbation
        if rng.random() < 0.4:
            node = rng.choice(child.nodes[4:])  # Skip inputs
            node.bias += rng.gauss(0, 0.2)
            node.bias = max(-3.0, min(3.0, node.bias))

        # Activation change
        if rng.random() < 0.2:
            node = rng.choice(child.nodes[4:])
            node.activation = rng.choice(ACTIVATION_NAMES)

        # Add connection
        if rng.random() < 0.15:
            src_candidates = [n.node_id for n in child.nodes]
            dst_candidates = [n.node_id for n in child.nodes if n.node_id >= 4]
            src = rng.choice(src_candidates)
            dst = rng.choice(dst_candidates)
            if src != dst:
                # Check no duplicate
                existing = {(c.src, c.dst) for c in child.connections}
                if (src, dst) not in existing:
                    child.connections.append(
                        CPPNConnection(src=src, dst=dst, weight=rng.gauss(0, 1.0))
                    )

        # Add node (split a connection)
        if rng.random() < 0.10 and child.connections:
            conn = rng.choice([c for c in child.connections if c.enabled])
            conn.enabled = False
            new_id = child._next_node_id
            child._next_node_id += 1
            act = rng.choice(ACTIVATION_NAMES)
            child.nodes.append(CPPNNode(node_id=new_id, activation=act))
            child.connections.append(CPPNConnection(src=conn.src, dst=new_id, weight=1.0))
            child.connections.append(CPPNConnection(src=new_id, dst=conn.dst, weight=conn.weight))

        # Toggle connection
        if rng.random() < 0.10 and child.connections:
            conn = rng.choice(child.connections)
            conn.enabled = not conn.enabled

        return child

    def evaluate(self, width: int, height: int) -> np.ndarray:
        """Evaluate the CPPN on a grid of (x, y) coordinates.

        Returns:
            Array of shape (height, width, n_outputs) with values in [0, 1].
        """
        # Create coordinate grid
        xs = np.linspace(-1, 1, width)
        ys = np.linspace(-1, 1, height)
        X, Y = np.meshgrid(xs, ys)
        D = np.sqrt(X**2 + Y**2)
        B = np.ones_like(X)

        # Flatten for batch processing
        n_pixels = width * height
        inputs = {
            0: X.flatten(),
            1: Y.flatten(),
            2: D.flatten(),
            3: B.flatten(),
        }

        # Build adjacency for topological sort
        node_ids = {n.node_id for n in self.nodes}
        node_map = {n.node_id: n for n in self.nodes}

        # Topological sort using Kahn's algorithm
        in_degree = {nid: 0 for nid in node_ids}
        adj = {nid: [] for nid in node_ids}
        for conn in self.connections:
            if conn.enabled and conn.src in node_ids and conn.dst in node_ids:
                adj[conn.src].append(conn)
                in_degree[conn.dst] = in_degree.get(conn.dst, 0) + 1

        queue = [nid for nid in node_ids if in_degree[nid] == 0]
        order = []
        while queue:
            nid = queue.pop(0)
            order.append(nid)
            for conn in adj.get(nid, []):
                in_degree[conn.dst] -= 1
                if in_degree[conn.dst] == 0:
                    queue.append(conn.dst)

        # Evaluate nodes in topological order
        values = dict(inputs)
        for nid in order:
            if nid < 4:
                continue  # Input nodes already set
            # Sum weighted inputs
            total = np.zeros(n_pixels)
            for conn in self.connections:
                if conn.enabled and conn.dst == nid and conn.src in values:
                    total += conn.weight * values[conn.src]
            # Apply activation
            node = node_map[nid]
            values[nid] = node.activate(total)

        # Collect outputs
        output = np.zeros((n_pixels, self.n_outputs))
        for i in range(self.n_outputs):
            out_id = 4 + i
            if out_id in values:
                output[:, i] = values[out_id]
            else:
                output[:, i] = 0.5

        # Normalize to [0, 1]
        output = (output - output.min(axis=0)) / (output.max(axis=0) - output.min(axis=0) + 1e-8)

        return output.reshape(height, width, self.n_outputs)

    def render(self, width: int = 64, height: int = 64) -> Image.Image:
        """Render the CPPN to an RGBA image."""
        data = self.evaluate(width, height)

        if self.n_outputs == 1:
            # Grayscale
            gray = (data[:, :, 0] * 255).astype(np.uint8)
            return Image.fromarray(gray, "L").convert("RGBA")
        elif self.n_outputs >= 3:
            # RGB
            rgb = (data[:, :, :3] * 255).astype(np.uint8)
            alpha = np.full((height, width), 255, dtype=np.uint8)
            rgba = np.dstack([rgb, alpha])
            return Image.fromarray(rgba, "RGBA")

    def to_dict(self) -> dict:
        """Serialize genome to dict for JSON storage."""
        return {
            "nodes": [
                {"id": n.node_id, "activation": n.activation, "bias": n.bias}
                for n in self.nodes
            ],
            "connections": [
                {"src": c.src, "dst": c.dst, "weight": c.weight, "enabled": c.enabled}
                for c in self.connections
            ],
            "n_inputs": self.n_inputs,
            "n_outputs": self.n_outputs,
            "fitness": self.fitness,
            "_next_node_id": self._next_node_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CPPNGenome":
        """Deserialize genome from dict."""
        genome = cls(
            n_inputs=d.get("n_inputs", 4),
            n_outputs=d.get("n_outputs", 3),
            fitness=d.get("fitness", 0.0),
            _next_node_id=d.get("_next_node_id", 0),
        )
        for nd in d["nodes"]:
            genome.nodes.append(CPPNNode(
                node_id=nd["id"],
                activation=nd.get("activation", "identity"),
                bias=nd.get("bias", 0.0),
            ))
        for cd in d["connections"]:
            genome.connections.append(CPPNConnection(
                src=cd["src"],
                dst=cd["dst"],
                weight=cd.get("weight", 1.0),
                enabled=cd.get("enabled", True),
            ))
        return genome


# ── CPPN Evolution (MAP-Elites style) ────────────────────────────────────────

@dataclass
class CPPNArchiveCell:
    """A cell in the MAP-Elites archive."""
    genome: CPPNGenome
    fitness: float
    image: Optional[Image.Image] = None
    features: tuple = ()


class CPPNEvolver:
    """Evolve CPPNs using MAP-Elites for diverse texture generation.

    MAP-Elites maintains a grid of the best genomes found for each
    combination of behavioral features, ensuring diversity.

    Behavioral features used:
      - Symmetry score (0-1): how symmetric the pattern is
      - Complexity score (0-1): edge density / detail level
      - Color diversity (0-1): number of distinct hues used

    This produces a diverse archive of textures that can be used as
    material libraries for SDF sprites.
    """

    def __init__(
        self,
        grid_dims: tuple[int, ...] = (5, 5, 5),
        seed: int = 42,
    ):
        self.grid_dims = grid_dims
        self.rng = random.Random(seed)
        self.archive: dict[tuple, CPPNArchiveCell] = {}
        self.total_evaluated = 0

    def _compute_features(self, image: Image.Image) -> tuple[float, float, float]:
        """Compute behavioral features for MAP-Elites binning.

        Returns (symmetry, complexity, color_diversity) each in [0, 1].
        """
        arr = np.array(image.convert("RGB"), dtype=np.float32) / 255.0

        # Symmetry: compare left half with flipped right half
        h, w, _ = arr.shape
        left = arr[:, :w//2, :]
        right = arr[:, w//2:w//2 + w//2, :][:, ::-1, :]
        min_w = min(left.shape[1], right.shape[1])
        symmetry = 1.0 - np.mean(np.abs(left[:, :min_w] - right[:, :min_w]))

        # Complexity: Laplacian edge density
        gray = np.mean(arr, axis=2)
        laplacian = np.abs(
            gray[1:-1, 1:-1] * 4
            - gray[:-2, 1:-1] - gray[2:, 1:-1]
            - gray[1:-1, :-2] - gray[1:-1, 2:]
        )
        complexity = min(1.0, np.mean(laplacian) * 10)

        # Color diversity: number of distinct hue bins
        from colorsys import rgb_to_hsv
        flat = arr.reshape(-1, 3)
        # Sample for speed
        if len(flat) > 1000:
            idx = self.rng.sample(range(len(flat)), 1000)
            flat = flat[idx]
        hues = []
        for r, g, b in flat:
            h_val, s, v = rgb_to_hsv(float(r), float(g), float(b))
            if s > 0.1 and v > 0.1:
                hues.append(h_val)
        if hues:
            hist, _ = np.histogram(hues, bins=12, range=(0, 1))
            color_diversity = np.count_nonzero(hist) / 12.0
        else:
            color_diversity = 0.0

        return (symmetry, complexity, color_diversity)

    def _features_to_cell(self, features: tuple[float, ...]) -> tuple[int, ...]:
        """Map continuous features to discrete grid cell."""
        cell = []
        for i, f in enumerate(features):
            dim = self.grid_dims[i] if i < len(self.grid_dims) else 5
            idx = int(np.clip(f * dim, 0, dim - 1))
            cell.append(idx)
        return tuple(cell)

    def _evaluate_fitness(self, image: Image.Image) -> float:
        """Evaluate aesthetic fitness of a CPPN texture.

        Combines:
          - Color harmony (hue distribution)
          - Contrast (dynamic range)
          - Pattern coherence (spatial autocorrelation)
        """
        arr = np.array(image.convert("RGB"), dtype=np.float32) / 255.0

        # Contrast
        gray = np.mean(arr, axis=2)
        contrast = gray.max() - gray.min()

        # Spatial coherence (not too noisy, not too uniform)
        # Ideal: medium autocorrelation
        shifted = np.roll(gray, 1, axis=1)
        autocorr = np.mean(gray * shifted)
        mean_sq = np.mean(gray) ** 2
        coherence = 1.0 - abs(autocorr - mean_sq - 0.1) * 5
        coherence = max(0, min(1, coherence))

        # Color richness
        std_r = np.std(arr[:, :, 0])
        std_g = np.std(arr[:, :, 1])
        std_b = np.std(arr[:, :, 2])
        color_richness = min(1.0, (std_r + std_g + std_b) / 0.6)

        # Combined fitness
        fitness = 0.3 * contrast + 0.3 * coherence + 0.4 * color_richness
        return float(np.clip(fitness, 0, 1))

    def run(
        self,
        n_iterations: int = 200,
        initial_population: int = 50,
        render_size: int = 64,
        verbose: bool = True,
    ) -> dict[tuple, CPPNArchiveCell]:
        """Run MAP-Elites evolution.

        Args:
            n_iterations: Total number of evaluations.
            initial_population: Number of random genomes to seed the archive.
            render_size: Size of rendered textures.
            verbose: Print progress.

        Returns:
            The MAP-Elites archive mapping cell coordinates to best genomes.
        """
        if verbose:
            print(f"CPPN MAP-Elites: {n_iterations} iterations, "
                  f"grid={self.grid_dims}")

        # Phase 1: Seed archive with random genomes
        for i in range(initial_population):
            genome = CPPNGenome.create_minimal(n_outputs=3, seed=self.rng.randint(0, 999999))
            image = genome.render(render_size, render_size)
            fitness = self._evaluate_fitness(image)
            features = self._compute_features(image)
            cell = self._features_to_cell(features)

            genome.fitness = fitness
            if cell not in self.archive or fitness > self.archive[cell].fitness:
                self.archive[cell] = CPPNArchiveCell(
                    genome=genome, fitness=fitness, image=image, features=features
                )
            self.total_evaluated += 1

        if verbose:
            print(f"  Seeded: {len(self.archive)} cells filled "
                  f"(of {np.prod(self.grid_dims)} possible)")

        # Phase 2: Evolve by mutating archive members
        for i in range(n_iterations - initial_population):
            # Select random genome from archive
            cells = list(self.archive.values())
            parent = self.rng.choice(cells).genome

            # Mutate
            child = parent.mutate(self.rng)
            try:
                image = child.render(render_size, render_size)
            except Exception:
                continue

            fitness = self._evaluate_fitness(image)
            features = self._compute_features(image)
            cell = self._features_to_cell(features)

            child.fitness = fitness
            if cell not in self.archive or fitness > self.archive[cell].fitness:
                self.archive[cell] = CPPNArchiveCell(
                    genome=child, fitness=fitness, image=image, features=features
                )

            self.total_evaluated += 1

            if verbose and (i + 1) % 50 == 0:
                print(f"  Iter {i + initial_population + 1}: "
                      f"{len(self.archive)} cells, "
                      f"best={max(c.fitness for c in self.archive.values()):.3f}")

        if verbose:
            print(f"  Done: {len(self.archive)} cells filled, "
                  f"{self.total_evaluated} total evaluated")

        return self.archive

    def get_best_textures(self, n: int = 10) -> list[tuple[Image.Image, float, tuple]]:
        """Get the top-N textures from the archive by fitness."""
        cells = sorted(self.archive.values(), key=lambda c: c.fitness, reverse=True)
        return [
            (c.image, c.fitness, c.features)
            for c in cells[:n]
        ]

    def export_texture_atlas(
        self,
        output_path: str,
        tile_size: int = 64,
        cols: int = 8,
    ) -> Image.Image:
        """Export the archive as a texture atlas image.

        Arranges the best textures in a grid, sorted by fitness.
        """
        cells = sorted(self.archive.values(), key=lambda c: c.fitness, reverse=True)
        n = len(cells)
        rows = (n + cols - 1) // cols

        atlas = Image.new("RGBA", (cols * tile_size, rows * tile_size), (0, 0, 0, 0))
        for i, cell in enumerate(cells):
            r, c_idx = divmod(i, cols)
            img = cell.image
            if img.size != (tile_size, tile_size):
                img = img.resize((tile_size, tile_size), Image.NEAREST)
            atlas.paste(img, (c_idx * tile_size, r * tile_size))

        atlas.save(output_path)
        return atlas
