"""
Compare rule-based vs embedding-based detection of bad chunks.
Simulates the bug: a complete article split in half (like Path B blind cut).
"""
import re
import json
import random
import numpy as np
from sentence_transformers import SentenceTransformer

ART_RE = re.compile(r"第[一二三四五六七八九十百千零0-9]+条")
SENT_END_RE = re.compile(r"[。？！；\n]$")

CITATION_PATTERN = re.compile(r"^第[一二三四五六七八九十百千零0-9]+条(?:的|规定|所)")


def has_own_article_marker_at_start(chunk):
    """Chunk starts with '第X条' that is its own content (not a citation like '第X条规定的')"""
    stripped = chunk.strip()
    m = ART_RE.match(stripped)
    if not m:
        return False
    after = stripped[m.end():]
    # If immediately followed by citation words, it's a reference
    if re.match(r"^(?:的|规定|所)", after):
        return False
    return True



def ends_at_boundary(chunk):
    """Check if chunk ends at a sentence boundary"""
    return bool(SENT_END_RE.search(chunk))


def rule_detect_v1(chunk):
    """V1: simple start/end boundary check"""
    stripped = chunk.strip()
    has_article = ART_RE.search(stripped) is not None
    starts_ok = has_article or bool(re.match(r"^[A-Za-z\u4e00-\u9fff]", stripped))
    ends_ok = bool(SENT_END_RE.search(stripped))
    return not starts_ok or not ends_ok


def rule_detect_v2(chunk):
    """V2: own article marker at start + end boundary"""
    stripped = chunk.strip()
    own_marker = has_own_article_marker_at_start(stripped)
    ends_ok = bool(SENT_END_RE.search(stripped))
    return not own_marker or not ends_ok


def rule_detect_v3(chunk):
    """V3: strict - must start with own article marker OR end properly"""
    stripped = chunk.strip()
    own_marker = has_own_article_marker_at_start(stripped)
    ends_ok = bool(SENT_END_RE.search(stripped))
    return not (own_marker and ends_ok)


def split_article_at(article, split_ratio=0.6):
    """Split an article at roughly 60% of its length (simulating blind cut)"""
    split_point = int(len(article) * split_ratio)
    # Try to find a sentence boundary near the split point
    near = article[max(0, split_point-10):split_point+10]
    # Just hard split
    part_a = article[:split_point]
    part_b = article[split_point:]
    return part_a, part_b


def main():
    print("Loading BGE-M3...")
    model = SentenceTransformer("BAAI/bge-m3")

    # Load articles from 劳动合同法
    import os as _os
    data_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "data", "raw")
    target = _os.path.join(data_dir, "中华人民共和国劳动合同法.md")
    with open(target, encoding="utf-8") as f:
        text = f.read()

    matches = list(ART_RE.finditer(text))
    starts = [m.start() for m in matches]

    articles = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        article = text[s:e].strip()
        articles.append(article)

    # Filter: candidates that can reasonably be split (40 < len < 790)
    candidates = [a for a in articles if 40 < len(a) < 790]

    # Also include some longer articles that would hit _fit_window
    long_articles = [a for a in articles if 790 <= len(a) <= 1600]

    print(f"\nTotal articles: {len(articles)}")
    print(f"Candidates (40-790 chars, can be split): {len(candidates)}")
    print(f"Long articles (790-1600 chars): {len(long_articles)}")

    # Sample for experiment
    random.seed(42)
    sample_candidates = random.sample(candidates, min(30, len(candidates)))
    sample_long = random.sample(long_articles, min(4, len(long_articles)))

    # Build test cases
    good_chunks = sample_candidates  # complete articles = good chunks
    bad_chunks = []

    # Create bad chunks by splitting articles
    for article in sample_candidates:
        part_a, part_b = split_article_at(article, split_ratio=0.6)
        if len(part_b.strip()) > 20:  # only keep meaningful fragments
            bad_chunks.append(part_b.strip())  # this is the fragment that loses identity

    # Create bad-good pairs (adjacent fragments from same article)
    pairs = []
    for article in sample_candidates:
        part_a, part_b = split_article_at(article, split_ratio=0.6)
        if len(part_b.strip()) > 20:
            pairs.append((part_a.strip(), part_b.strip()))

    print(f"\nGood chunks (complete articles): {len(good_chunks)}")
    print(f"Bad chunks (split fragments): {len(bad_chunks)}")
    print(f"Adjacent pairs: {len(pairs)}")

    # === Rule-based detection ===
    variants = {
        "V1 (start/end boundary)": rule_detect_v1,
        "V2 (own marker at start)": rule_detect_v2,
        "V3 (marker + end boundary)": rule_detect_v3,
    }

    print(f"\n=== Rule-based detection ===")
    for name, func in variants.items():
        good_hits = sum(func(c) for c in good_chunks)
        bad_hits = sum(func(c) for c in bad_chunks)
        print(f"  {name}:")
        print(f"    Good flagged bad: {good_hits}/{len(good_chunks)} (false positive)")
        print(f"    Bad  flagged bad: {bad_hits}/{len(bad_chunks)} (true positive)")

    # Show specific bad chunks that were missed vs caught
    print(f"\n=== Detailed bad chunk analysis ===")
    for i, chunk in enumerate(bad_chunks[:10]):
        v1 = rule_detect_v1(chunk)
        v2 = rule_detect_v2(chunk)
        v3 = rule_detect_v3(chunk)
        print(f"\n  [{i}] len={len(chunk)} v1={v1} v2={v2} v3={v3}")
        print(f"       '{chunk[:100]}'")

    # === Embedding-based analysis ===
    print(f"\n=== Embedding-based analysis ===")
    print("Encoding chunks...")

    good_embeds = model.encode(good_chunks, normalize_embeddings=True)
    bad_embeds = model.encode(bad_chunks, normalize_embeddings=True)

    # 1. Intra-chunk similarity: average pairwise cosine within each group
    # Are good chunks more "dispersed" than bad chunks?
    good_intra_sim = np.triu(good_embeds @ good_embeds.T, k=1)
    bad_intra_sim = np.triu(bad_embeds @ bad_embeds.T, k=1)

    good_mean = good_intra_sim[good_intra_sim != 0].mean()
    bad_mean = bad_intra_sim[bad_intra_sim != 0].mean()

    print(f"\n1. Intra-group mean cosine similarity:")
    print(f"   Good chunks among themselves: {good_mean:.4f}")
    print(f"   Bad chunks among themselves: {bad_mean:.4f}")
    print(f"   -> {'Bad chunks are MORE similar to each other' if bad_mean > good_mean else 'Bad chunks are LESS similar'}")

    # 2. Adjacent pair similarity: how similar is part_a to part_b?
    # High similarity = this pair was cut at a bad place
    pair_sims = []
    for part_a, part_b in pairs:
        ea = model.encode([part_a], normalize_embeddings=True)
        eb = model.encode([part_b], normalize_embeddings=True)
        sim = float((ea * eb).sum(axis=1)[0])
        pair_sims.append(sim)

    pair_sims = np.array(pair_sims)
    print(f"\n2. Adjacent pair (part_a vs part_b) cosine similarity:")
    print(f"   Mean: {pair_sims.mean():.4f}")
    print(f"   Std: {pair_sims.std():.4f}")
    print(f"   Min: {pair_sims.min():.4f}")
    print(f"   Max: {pair_sims.max():.4f}")

    # 3. Compare: for each complete article, compute similarity to a random DIFFERENT article
    # This simulates the expected similarity between properly-separated chunks
    random_pairs = []
    for i, a in enumerate(sample_candidates):
        others = [o for j, o in enumerate(sample_candidates) if j != i]
        other = random.choice(others)
        random_pairs.append((a, other))

    random_pair_sims = []
    for a1, a2 in random_pairs:
        e1 = model.encode([a1], normalize_embeddings=True)
        e2 = model.encode([a2], normalize_embeddings=True)
        sim = float((e1 * e2).sum(axis=1)[0])
        random_pair_sims.append(sim)

    random_pair_sims = np.array(random_pair_sims)

    print(f"\n3. Cross-article similarity (different articles):")
    print(f"   Mean: {random_pair_sims.mean():.4f}")
    print(f"   Std: {random_pair_sims.std():.4f}")

    print(f"\n4. Separability (adjacent-pair vs cross-article):")
    # Overlap between distributions - if clear gap, embedding-based works
    overlap_threshold = (pair_sims.mean() + random_pair_sims.mean()) / 2
    # How many adjacent pairs are MORE similar than the average cross-article pair?
    ratio_above = (pair_sims > random_pair_sims.mean()).mean()
    print(f"   Adjacent-pair mean: {pair_sims.mean():.4f}")
    print(f"   Cross-article mean: {random_pair_sims.mean():.4f}")
    print(f"   Adjacent pairs ABOVE cross-article mean: {ratio_above:.1%}")
    print(f"   Separation ratio: {pair_sims.mean() / random_pair_sims.mean():.2f}x")

    print(f"\n5. Conclusion")


if __name__ == "__main__":
    main()
