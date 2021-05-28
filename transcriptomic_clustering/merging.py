from typing import Any, Tuple, Dict, List, Optional
import anndata as ad
import pandas as pd
import numpy as np
from scipy.spatial.distance import pdist, squareform
import logging
from collections import defaultdict
import warnings
import transcriptomic_clustering as tc

def merge_clusters(
        adata_norm,
        adata_reduced,
        cluster_assignments: Dict[Any, np.ndarray],
        cluster_by_obs: np.ndarray,
        min_cluster_size,
        k,
        low_th,
        de_method,
        chunk_size):

    # TODO: Add all the thresholds

    # Calculate cluster means on reduced space
    cl_means_reduced, _ = tc.get_cluster_means(adata_reduced, cluster_assignments, cluster_by_obs, chunk_size, low_th)

    # Merge small clusters
    merge_small_clusters(cl_means_reduced, cluster_assignments, min_cluster_size)

    # TODO: Create new cluster_by_obs based on updated cluster assignments

    # Calculate cluster means on normalized data
    cl_means, present_cl_means = tc.get_cluster_means(adata_norm, cluster_assignments, cluster_by_obs, chunk_size, low_th)

    # Merge remaining clusters by differential expression
    while len(cluster_assignments.keys()) > 1:
        # If only two clusters left, merge them
        if len(cluster_assignments.keys()) is 2:
            cl_labels = cluster_assignments.keys()
            merge_two_clusters(cluster_assignments, cl_labels[1], cl_labels[0], cl_means_reduced)
            break

        # Use updated cluster means in reduced space to get nearest neighbors for each cluster
        # Steps 1-3
        neighbor_pairs = get_k_nearest_clusters(cl_means_reduced, k)

        if len(neighbor_pairs) is 0:
            break

        # TODO: Step 4: Get DE for pairs based on de_method

        # TODO: Step 5: If not de score > threshold for all comparisons, merge clusters with lowest de scores. One all greater than threshold, this is done
        # From R code: The first pair in to.merge always merge. For the remaining pairs, if both clusters have already enough cells,
        # or independent of previus merging, then they can be directly merged as well, without re-assessing DE genes
        # This does not quite make sense to me.

        # TODO: Compute marker differential expressed genes based on function param

        # Returns
        # - updated cluster assignments
        # - differentially expressed genes
        # - final cluster pairwise de.score
        # - top cluster pairwise markers



def merge_two_clusters(
        cluster_assignments: Dict[Any, np.ndarray],
        label_source: Any,
        label_dest: Any,
        cluster_means: pd.DataFrame,
        present_cluster_means: pd.DataFrame=None
):
    """
    Merge source cluster into a destination cluster by:
    1. updating cluster means
    2. updating mean of expressions present if not None
    3. updating cluster assignments

    Parameters
    ----------
    cluster_assignments:
        map of cluster label to cell idx belonging to cluster
    label_source:
        label of cluster being merged
    label_dest:
        label of cluster merged into
    cluster_means:
        dataframe of cluster means indexed by cluster label
    present_cluster_means:
        dataframe of cluster means indexed by cluster label filtered by low_th

    Returns
    -------
    """

    merge_cluster_means(cluster_means, cluster_assignments, label_source, label_dest)

    if present_cluster_means is not None:
        merge_cluster_means(present_cluster_means, cluster_assignments, label_source, label_dest)

    # merge cluster assignments
    cluster_assignments[label_dest] += cluster_assignments[label_source]
    cluster_assignments.pop(label_source)


def merge_cluster_means(
        cluster_means: pd.DataFrame,
        cluster_assignments: Dict[Any, np.ndarray],
        label_source: Any,
        label_dest: Any
):
    """
    Merge source cluster into a destination cluster by:
    1. computing the updated cluster centroid (mean gene expression)
        of the destination cluster
    2. deleting source cluster after merged

    Parameters
    ----------
    cluster_means:
        dataframe of cluster means indexed by cluster label
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

    cluster_means.loc[label_dest] = (cluster_means.loc[label_source] * n_source +
                                     cluster_means.loc[label_dest] * n_dest
                                    ) / (n_source + n_dest)

    # remove merged cluster
    cluster_means.drop(label_source, inplace=True)


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
        cluster_means: pd.DataFrame,
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
        dataframe of cluster means indexed by cluster label
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
        similarity_small_to_all_df = calculate_similarity(
            cluster_means,
            group_rows=small_cluster_labels,
            group_cols=all_cluster_labels)

        source_label, dest_label, max_similarity = find_most_similar(
            similarity_small_to_all_df,
        )
        logging.info(f"Merging small cluster {source_label} into {dest_label} -- similarity: {max_similarity}")
        merge_two_clusters(cluster_assignments, source_label, dest_label, cluster_means)

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
