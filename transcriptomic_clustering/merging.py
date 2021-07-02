from typing import Any, Tuple, Dict, List, Optional
import anndata as ad
import pandas as pd
import numpy as np
from scipy.spatial.distance import pdist, squareform
import logging
from collections import defaultdict
import warnings
import transcriptomic_clustering as tc

DEFAULT_THRESHOLDS = {
    'q1_thresh': 0.5,
    'q2_thresh': None,
    'cluster_size_thresh': 6,
    'qdiff_thresh': 0.7,
    'padj_thresh': 0.05,
    'lfc_thresh': 1.0,
    'score_thresh': 150,
    'low_thresh': 1
}


def merge_clusters(
        adata_norm: ad.AnnData,
        adata_reduced: ad.AnnData,
        cluster_assignments: Dict[Any, np.ndarray],
        cluster_by_obs: np.ndarray,
        thresholds: Dict[str, Any] = DEFAULT_THRESHOLDS,
        k: Optional[int] = 2,
        de_method: Optional[str] = 'chisq',
        chunk_size: Optional[int] = None
) -> Dict[Any, np.ndarray]:
    """
    Merge clusters based on size and differential gene expression score

    1. merge small clusters
    2. merge clusters based by differential gene expression score

    Parameters
    ----------
    adata_norm:
        AnnData object of normalized data
    adata_reduced:
        AnnData object in reduced space
    cluster_assignments:
        map of cluster label to cell idx belonging to cluster
    cluster_by_obs:
        array of cells with cluster value
    thresholds:
        threshold for de calculation
    k:
        number of cluster neighbors
    de_method:
        method used for de calculation
    chunk_size:
        number of observations to process in a single chunk

    Returns
    -------
    cluster_assignments:
        updated mapping of cluster assignments
    """

    # Calculate cluster means on reduced space
    cl_means_reduced, _, _ = tc.get_cluster_means(adata_reduced,
                                               cluster_assignments,
                                               cluster_by_obs,
                                               chunk_size,
                                               low_th=thresholds['low_thresh'])

    # Merge small clusters
    min_cluster_size = thresholds['cluster_size_thresh']
    cluster_assignments_merge = cluster_assignments.copy()
    merge_small_clusters(cl_means_reduced, cluster_assignments_merge, min_cluster_size)

    # Create new cluster_by_obs based on updated cluster assignments
    cluster_by_obs = np.zeros((adata_norm.shape[0],))
    for cl_id, idxs in cluster_assignments_merge.items():
        cluster_by_obs[idxs] = cl_id

    # Calculate cluster means on normalized data
    cl_means, present_cl_means, cl_vars = tc.get_cluster_means(adata_norm,
                                                      cluster_assignments_merge,
                                                      cluster_by_obs,
                                                      chunk_size,
                                                      low_th=thresholds['low_thresh'])

    # Merge remaining clusters by differential expression
    merge_clusters_by_de(cluster_assignments_merge,
                         cl_means,
                         cl_vars,
                         present_cl_means,
                         cl_means_reduced,
                         thresholds,
                         k,
                         de_method,
                         )

    return cluster_assignments_merge


def merge_two_clusters(
        cluster_assignments: Dict[Any, np.ndarray],
        label_source: Any,
        label_dest: Any,
        cluster_means: pd.DataFrame,
        cluster_variances: Optional[pd.DataFrame]=None,
        present_cluster_means: pd.DataFrame=None
):
    """
    Merge source cluster into a destination cluster by:
    1. updating cluster means and variances
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
    cluster_variances:
        dataframe of cluster variances indexed by cluster label
    present_cluster_means:
        dataframe of cluster means indexed by cluster label filtered by low_th

    Returns
    -------
    """

    merge_cluster_means_vars(cluster_assignments, label_source, label_dest, cluster_means, cluster_variances)

    if present_cluster_means is not None:
        merge_cluster_means_vars(cluster_assignments, label_source, label_dest, present_cluster_means, None)

    # merge cluster assignments
    cluster_assignments[label_dest] += cluster_assignments[label_source]
    cluster_assignments.pop(label_source)


def merge_cluster_means_vars(
        cluster_assignments: Dict[Any, np.ndarray],
        label_source: Any,
        label_dest: Any,
        cluster_means: pd.DataFrame,
        cluster_variances: Optional[pd.DataFrame]
):
    """
    Merge source cluster into a destination cluster by:
    1. computing the updated cluster centroid (mean gene expression)
        of the destination cluster
    2. compute updated variance
    3. deleting source cluster after merged

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
    cluster_variances:
        dataframe of cluster variances indexed by cluster label

    Returns
    -------
    """

    # update cluster means:
    n1 = len(cluster_assignments[label_source])
    n2 = len(cluster_assignments[label_dest])
    mean1 = cluster_means.loc[label_source]
    mean2 = cluster_means.loc[label_dest]

    mean_comb = (mean1 * n1 + mean2 * n2) / (n1 + n2)

    cluster_means.loc[label_dest] = mean_comb
    cluster_means.drop(label_source, inplace=True)

    if cluster_variances is not None:
        var1 = cluster_variances.loc[label_source]
        var2 = cluster_variances.loc[label_dest]
        
        var_comb = 1 / (n1 + n2 - 1) * (
            (n1 - 1) * var1 + n1 * (mean1 - mean_comb) ** 2 +
            (n2 - 1) * var2 + n1 * (mean2 - mean_comb) ** 2
        )

        cluster_variances.loc[label_dest] = var_comb
        cluster_variances.drop(label_source, inplace=True)


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
    cluster_assignments:
        updated mapping of cluster assignments
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



def merge_clusters_by_de(
    cluster_assignments: Dict[Any, np.ndarray],
    cluster_means: pd.DataFrame,
    cluster_variances: pd.DataFrame,
    present_cluster_means: pd.DataFrame,
    cluster_means_rd: pd.DataFrame,
    thresholds: Dict[str, Any],
    k: Optional[int] = 2,
    de_method: Optional[str] = 'chisq',
):
    """
    Merge clusters by the calculated gene differential expression score

    1. get k nearest clusters for each cluster in a reduced space
    2. calculate differential expression scores for all pairs
    3. sort scores by lowest and loop through them, merging pairs with scores lower than threshold

    Parameters
    ----------
    cluster_assignments:
        map of cluster label to cell idx belonging to cluster
    cluster_means:
        dataframe of cluster means indexed by cluster label in a normalized space
    cluster_variances:
        dataframe of cluster variances indexed by cluster label in a normalized space
    present_cluster_means:
        dataframe of cluster means indexed by cluster label filtered by low_th in a normalized space
    cluster_means_rd:
        dataframe of cluster means indexed by cluster label in a reduced space
    k:
        number of cluster neighbors
    de_method:
        method used for de calculation
    thresholds:
        threshold use de calculation

    Returns
    -------
    cluster_assignments:
        updated mapping of cluster assignments
    """

    cl_size = {k: len(v) for k, v in cluster_assignments.items()}

    score_th = thresholds.pop('score_thresh')
    thresholds.pop('low_thresh')

    while len(cluster_assignments.keys()) > 1:
        # Use updated cluster means in reduced space to get nearest neighbors for each cluster
        # Steps 1-3
        neighbor_pairs = get_k_nearest_clusters(cluster_means_rd, k)
        neighbor_pairs = order_pairs(neighbor_pairs)
        if len(neighbor_pairs) == 0:
            break

        # Step 4: Get DE for pairs based on de_method
        if de_method == 'ebayes':
            scores = tc.de_pairs_ebayes(
                neighbor_pairs,
                cluster_means,
                cluster_variances,
                present_cluster_means,
                cl_size,
                thresholds,
            )
        elif de_method == 'chisq':
            scores = tc.de_pairs_chisq(
                neighbor_pairs,
                cluster_means,
                present_cluster_means,
                cl_size,
                thresholds,
            )

        # Sort scores
        scores = scores.sort_values(by='score')

        # Peek at first score and if > threshold, they are all greater than threshold
        score = scores.iloc[0].score
        if score >= score_th:
            break

        # Merge pairs below threshold, skipping already merged clusters
        merged_clusters = set()
        for pair, row in scores.iterrows():
            score = row.score

            if score >= score_th:
                break

            dst_label, src_label = pair

            if dst_label in merged_clusters or src_label in merged_clusters:
                continue

            logging.info(f"Merging cluster {src_label} into {dst_label} -- de score: {score}")

            # Update cluster means on reduced space
            merge_cluster_means_vars(cluster_assignments, src_label, dst_label, cluster_means_rd, None)

            # Update cluster means and cluster assignments
            merge_two_clusters(cluster_assignments, src_label, dst_label, cluster_means, cluster_variances, present_cluster_means)
            merged_clusters.add(src_label)
            merged_clusters.add(dst_label)

            # Merge cluster sizes
            cl_size[dst_label] += cl_size[src_label]
            cl_size.pop(src_label)



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
        list of cluster pairs
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


def order_pairs(
        neighbor_pairs: List[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """
    Order each label such that smaller label is follower by a larger
    e.g: (3,8), (6,7)

    Parameters
    ----------
    neighbor_pairs:
        list of neighbor pairs

    Returns
    -------
    ordered list of neighbor pairs
    """
    ordered_pairs = []

    for p in neighbor_pairs:
        a, b = p
        if b > a:
            ordered_pairs.append((a, b))
        else:
            ordered_pairs.append((b, a))

    return ordered_pairs


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
