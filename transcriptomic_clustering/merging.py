from typing import Any, Tuple, Dict, List, Optional
import anndata as ad
import pandas as pd
import numpy as np
from scipy.spatial.distance import pdist, squareform
import logging
from collections import defaultdict
import warnings


def get_cluster_means(
        adata: ad.AnnData,
        cluster_assignments: Dict[Any, np.array]
) -> Dict[Any, np.array]:
    """
    Compute mean gene expression over cells belonging to each cluster

    Parameters
    ----------
    adata:
        AnnData with X matrix and annotations
    cluster_assignments:
        map of cluster label to cell idx

    Returns
    -------
    cluster_means:
        map of cluster label to mean expressions (array of size n_genes)
    """
    cluster_means = {}

    for label, idx in cluster_assignments.items():
        adata_view = adata[idx, :]
        X = adata_view.X
        cluster_means[label] = np.asarray(np.mean(X, axis=0)).ravel()

    return cluster_means


def merge_two_clusters(
        cluster_means: Dict[Any, np.ndarray],
        cluster_assignments: Dict[Any, np.ndarray],
        label_source: Any,
        label_dest: Any
):
    """
    Merge source cluster into a destination cluster by:
    1. computing the updated cluster centroid (mean gene expression)
        of the destination cluster
    2. update cluster assignments
    3. deleting small merged cluster

    Parameters
    ----------
    cluster_means:
        map of cluster label to mean cluster expressions
    cluster_assignments:
        map of cluster label to cell idx belonging to cluster
    label_source:
        label of cluster being merged
    label_dest:
        label of cluster merged into

    Returns
    -------

    """

    # update cluster means:
    n_source = len(cluster_assignments[label_source])
    n_dest = len(cluster_assignments[label_dest])

    cluster_means[label_dest] = (cluster_means[label_source] * n_source +
                                 cluster_means[label_dest] * n_dest
                                 ) / (n_source + n_dest)

    # update cluster assignments:
    cluster_assignments[label_dest] += cluster_assignments[label_source]

    # remove merged cluster
    cluster_means.pop(label_source)
    cluster_assignments.pop(label_source)


def pdist_normalized(
        X: np.ndarray
) -> np.ndarray:
    """
    Calculate similarity metric as (1 - pairwise_distance/max_distance)

    Parameters
    ----------
    X:
        An m by n array of m clusters in an n-dimensional space
    Returns
    -------
    similarity:
        measure of similarity
    """

    dist = squareform(pdist(X))
    dist_norm = dist / np.max(dist)

    return 1 - dist_norm


def calculate_similarity(
        cluster_means: pd.DataFrame,
        group_rows: List[Any],
        group_cols: List[Any]
) -> pd.DataFrame:
    """
    Calculate similarity measure between two cluster groups (group_rows, group_cols)
    based on cluster means (cluster centroids)
    If data has more than 2 dimensions use correlation coefficient as a measure of similarity
    else use normalized distance measure

    Parameters
    ----------
    cluster_means:
        Dataframe of cluster means with cluster labels as index
    group_rows:
        cluster group with clusters being merged
    group_cols:
        cluster group with destination clusters
    Returns
    -------
    similarity:
        array of similarity measure
    """

    cluster_labels_subset = set(group_rows + group_cols)
    means = cluster_means.loc[cluster_labels_subset]
    n_clusters, n_vars = means.shape

    if n_vars > 2:
        similarity_df = means.T.corr()
    else:
        similarity = pdist_normalized(means)
        similarity_df = pd.DataFrame(similarity,
                                     index=cluster_labels_subset,
                                     columns=cluster_labels_subset)

    np.fill_diagonal(similarity_df.values, np.nan)

    return similarity_df.loc[group_rows][group_cols]


def find_most_similar(
        similarity_df: pd.DataFrame,
) -> Tuple[Any, Any, float]:
    """

    Parameters
    ----------
    similarity_df:
        similarity metric between clusters
    Returns
    -------
    source_label, dest_label, max_similarity:
        labels of the source and destination clusters and their similarity value
    """


    similarity_df = similarity_df.transpose()

    similarity_sorted = similarity_df.unstack().sort_values(ascending=False).dropna()
    source_label, dest_label = similarity_sorted.index[0]
    max_similarity = similarity_sorted[(source_label, dest_label)]

    return source_label, dest_label, max_similarity


def find_small_clusters(
        cluster_assignments: Dict[Any, np.ndarray],
        min_size: int
) -> List[Any]:

    return [k for (k, v) in cluster_assignments.items() if len(v) < min_size]


def merge_small_clusters(
        cluster_means: Dict[Any, np.ndarray],
        cluster_assignments: Dict[Any, np.ndarray],
        min_size: int,
):
    """
    Then merge small clusters (with size < min_size) iteratively as:

    1. calculate similarity between small and all clusters
    2. merge most-highly similar small cluster
    3. update list of small/all clusters
    4. go to 1 until all small clusters are merged

    Parameters
    ----------
    cluster_means:
        map of cluster label to mean cluster expressions
    cluster_assignments:
        map of cluster label to cell idx belonging to cluster
    min_size:
        smallest size that is not merged

    Returns
    -------
    """
    all_cluster_labels = list(cluster_assignments.keys())
    small_cluster_labels = find_small_clusters(cluster_assignments, min_size=min_size)

    while small_cluster_labels:
        cluster_means_df = pd.DataFrame(np.vstack(list(cluster_means.values())), index=cluster_means.keys())
        similarity_small_to_all_df = calculate_similarity(
            cluster_means_df,
            group_rows=small_cluster_labels,
            group_cols=all_cluster_labels)

        source_label, dest_label, max_similarity = find_most_similar(
            similarity_small_to_all_df,
        )
        logging.info(f"Merging small cluster {source_label} into {dest_label} -- similarity: {max_similarity}")
        merge_two_clusters(cluster_means, cluster_assignments, source_label, dest_label)

        # update labels:
        small_cluster_labels = find_small_clusters(cluster_assignments, min_size=min_size)
        all_cluster_labels = list(cluster_assignments.keys())


def get_k_nearest_clusters(
        cluster_means: pd.DataFrame,
        k: Optional[int] = 2
) -> List[Tuple[int, int]]:
    """
    Get k nearest neighbors for each cluster

    Parameters
    ----------
    cluster_means:
        dataframe of cluster means with cluster labels as index
    k:
        number of nearest neighbors

    Returns
    -------
    nearest_neighbors:
        list of similarity measure
    """

    cluster_labels = list(cluster_means.index)

    if k >= len(cluster_labels):
        warnings.warn("k cannot be greater than or the same as the number of clusters. "
                          "Defaulting to 2.")
        k = 2

    similarity = calculate_similarity(
            cluster_means,
            group_rows=cluster_labels,
            group_cols=cluster_labels)

    similarity = similarity.unstack().dropna()

    # Get k nearest neighbors
    nearest_neighbors = set()
    for c in cluster_labels:
        # Sort similarities for a cluster
        sorted_similarities = similarity.loc[(c, )].sort_values(ascending=False)

        for i in range(k):
            neighbor_cl = sorted_similarities.index[i]

            # Make sure neighbor doesn't already exist
            if not (neighbor_cl, c) in nearest_neighbors:
                nearest_neighbors.add((c, neighbor_cl))

    return list(nearest_neighbors)


def get_cluster_assignments(
        adata: ad.AnnData,
        cluster_label_obs: str = "pheno_louvain"
) -> Dict[Any, np.ndarray]:
    """

    Parameters
    ----------
    adata:
        AnnData object with with obs including cluster label
    cluster_label_obs:
        cluster label annotations in adata.obs

    Returns
    -------
    cluster_assignments:
        map of cluster label to cell idx
    """
    if cluster_label_obs not in list(adata.obs):
        raise ValueError(f"column {cluster_label_obs} is missing from obs")

    cluster_assignments = defaultdict(list)
    for i, label in enumerate(adata.obs[cluster_label_obs]):
        cluster_assignments[label].append(i)

    return cluster_assignments
