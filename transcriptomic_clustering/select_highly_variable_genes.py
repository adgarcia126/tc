from typing import Optional

import numpy as np
import pandas as pd
import scanpy as sc
from scipy.sparse import csr_matrix, issparse
from skmisc.loess import loess
from scipy.stats import norm
from welford import Welford

from statsmodels.stats.multitest import fdrcorrection


def compute_z_scores(dispersion: np.ndarray):
    """
        Compute dispersion z-scores for each gene in a gene x sample matrix

        Parameters
        ----------
        dispersion: numpy array

        Returns
        -------
        z-scores: numpy array

    """
    q75, q25 = np.percentile(dispersion, [75 ,25])
    iqr = q75 - q25
    m_iqr = (q25 + q75)/2.0
    delta = iqr / (norm.ppf(0.75) - norm.ppf(0.25))
    
    return (dispersion  - m_iqr) / delta

def select_highly_variable_genes(adata: sc.AnnData = None,
            max_genes: Optional[int] = 3000,
            norm_cell_expression_file: str = None, 
            chunk_size: Optional[int] = 3000,
            inplace: bool = True
            ) -> Optional[pd.DataFrame]:
    """
        select highly variable genes using the method in scrattch.hicat that
        is based on brennecke’s method, which assumes the reads follow a negative binomial distribution, 
        in which case, using loess fit to fine a relationship between mean and dispersions

        Parameters
        ----------
        adata: log(CPM+1) normalization of cell expression (w/o logarithmized) in AnnData format (csr_matrix is supported)
            The annotated data matrix of shape n_obs × n_vars.
            Rows correspond to cells and columns to genes
        max_genes: number of highly variable genes to keep
        norm_cell_expression_file: file name of the normalization log(CPM+1) of cell expression
        chunk_size: size of the chunk
        inplace: whether to place calculated metrics in `.var` or return them.

        Returns
        -------
        Depending on `inplace` returns calculated metrics (:class:`pandas.DataFrame`) or
        updates `.var` with the following fields

        highly_variable: boolean indicator of highly-variable genes
        p_adj: p-adjust per gene
        z_score: z-score per gene
        means: means per gene
        dispersions: dispersions per gene

    """
    if adata is not None:
        # select highly variable genes without chunking
        if not isinstance(adata, sc.AnnData):
            raise ValueError('`select_highly_variable_genes` expects an `AnnData` argument')

        if issparse(adata.X):
            if not isinstance(adata.X, csr_matrix):
                raise ValueError("Unsupported format for cell_expression matrix. Must be in CSR format")

        # cpm normalization
        x_cpm = np.expm1(adata.X)

        # means, variances
        means, variances = sc.pp._utils._get_mean_var(x_cpm)

    elif norm_cell_expression_file is not None:
        #  select highly variable genes with chunking
        w_mat = Welford()

        adata = sc.read_h5ad(norm_cell_expression_file, backed='r')

        for chunk, start, end in adata.chunked_X(chunk_size):
            obs_chunk = adata.obs[start:end]
            adata_chunk = sc.AnnData(chunk, obs=obs_chunk, var=adata.var)
            adata_chunk.X = np.expm1(adata_chunk.X)
            w_mat.add_all(adata_chunk.X.toarray())
            del adata_chunk

        adata.file.close()
        means, variances = w_mat.mean, w_mat.var_p

    else:
        raise ValueError("Either AnnData object or file name of the normalization need to be provided")

    # dispersions
    dispersions = np.log(variances / (means + 1e-10) + 1)

    # z-scores
    z_scores = compute_z_scores(dispersions)

    # Loess regression
    x = np.log(means+1)
    y = dispersions

    loess_regression = loess(x, y)
    loess_regression.fit()

    loess_fit = loess_regression.outputs

    # p values
    loess_z = compute_z_scores(loess_fit.fitted_residuals)
    p_vals = 1 - norm.cdf(loess_z)
    
    # p.adjust using BH method
    rejected,p_adj = fdrcorrection(p_vals)

    # select highly variable genes
    qval_indices = [i_gene for i_gene, padj_val in enumerate(p_adj) if padj_val < 1]

    df = pd.DataFrame(index=qval_indices)

    df['gene'] = adata.var_names[qval_indices]
    df['p_adj'] = p_adj[qval_indices]
    df['z_score'] = z_scores[qval_indices]
    df['means_log'] = x[qval_indices]
    df['dispersions_log'] = dispersions[qval_indices]
    
    df.sort_values(
        ['p_adj', 'z_score'],
        ascending=[True, False],
        na_position='last',
        inplace=True,
    )

    hvg_list = df['gene'][0:max_genes].tolist()

    hvg_dict = {}
    for iter_gene in adata.var_names:
        if iter_gene in hvg_list:
            hvg_dict[iter_gene] = True
        else:
            hvg_dict[iter_gene] = False

    if inplace:
        if norm_cell_expression_file is not None:
            adata = sc.read_h5ad(norm_cell_expression_file)

        adata.uns['hvg'] = {'flavor': 'hicat'}
        adata.var['highly_variable'] = pd.Series(data=hvg_dict, index=hvg_dict.keys())
        adata.var['p_adj'] = df['p_adj'].values
        adata.var['z_score'] = df['z_score'].values
        adata.var['means_log'] = df['means_log'].values
        adata.var['dispersions_log'] = df['dispersions_log'].values
    else:
        df = df[0:max_genes]
        df['highly_variable'] = True
        return df

