"""
Starts with some midi files, parses, pre-processes then invokes a large linear fit.
For speed, work continues in export.py, wherein this data is spat out in usable form.
"""
import os
import tables
from random import sample
import warnings
import numpy as np
import scipy as sp
from stats_utils import lik_test, log_lik_ratio, square_feature, triangle_feature
from scipy.sparse import coo_matrix, dok_matrix, csc_matrix
from preprocess_notes import get_recence_data
from serialization import write_sparse_hdf, read_sparse_hdf
from config import *

obs_meta_table = get_recence_data()
obs_meta_table = obs_meta_handle.get_node('/', 'note_obs_meta')
result = obs_meta_table.col('result')
n_obs = obs_meta_table.nrows
col_names = [r_name_for_p[p] for p in xrange(12)]


# TODO: Now that I have a better feature finder, could do this with broader features, or more features or sth
f0 = csc_matrix(
    (
        square_feature(obs_vec.data, MAX_AGE, 0.125),
        obs_vec.indices,
        obs_vec.indptr
    ), shape=(n_obs, n_basic_vars)
)
f0.eliminate_zeros()
f1 = csc_matrix(
    (
        square_feature(obs_vec.data, MAX_AGE-0.25, 0.125),
        obs_vec.indices,
        obs_vec.indptr
    ), shape=(n_obs, n_basic_vars)
)
f1.eliminate_zeros()
f2 = csc_matrix(
    (
        square_feature(obs_vec.data, MAX_AGE-0.5, 0.125),
        obs_vec.indices,
        obs_vec.indptr
    ), shape=(n_obs, n_basic_vars)
)
f2.eliminate_zeros()
f3 = csc_matrix(
    (
        square_feature(obs_vec.data, MAX_AGE-0.75, 0.125),
        obs_vec.indices,
        obs_vec.indptr
    ), shape=(n_obs, n_basic_vars)
)
f3.eliminate_zeros()
f4 = csc_matrix(
    (
        square_feature(obs_vec.data, MAX_AGE-1.0, 0.125),
        obs_vec.indices,
        obs_vec.indptr
    ), shape=(n_obs, n_basic_vars)
)
f4.eliminate_zeros()

#Now, let's manufacture compound features.
#first make everything sparse for consistent multiplying.
#Weird looking for the results, which we have to upcast from vector to amtrix
results_sparse = dok_matrix(
        obs_meta["result"].reshape((obs_meta["result"].size,)+(1,))
    ).tocsc()[:,0]

# In fact we include diameter by scaling success and renormalising; the Chi2 test should still hold in that case.
# this is only tenable if it has no interaction effects, i.e. chord span has no effect on note choice.

results_scaled = (obs_meta["diameter"]*obs_meta["result"]).astype('float32')
results_scaled = results_scaled/results_scaled.sum()
results_scaled_sparse = dok_matrix(
        results_scaled.reshape((results_scaled.size,)+(1,))
    ).tocsc()[:,0]

# expensive for the barcode, which is dense.
note_barcode_sparse = dok_matrix(obs_meta["barcode"]).tocsc()

#now, hold features in arrays for uniform access
feature_names = []
features = []

feature_names += ["F0" + r_name_for_i[i] for i in xrange(f0.shape[1])]
features += [f0[:, i] for i in xrange(f0.shape[1])]
feature_names += ["F1" + r_name_for_i[i] for i in xrange(f1.shape[1])]
features += [f1[:, i] for i in xrange(f1.shape[1])]
feature_names += ["F2" + r_name_for_i[i] for i in xrange(f2.shape[1])]
features += [f2[:, i] for i in xrange(f2.shape[1])]
feature_names += ["F3" + r_name_for_i[i] for i in xrange(f2.shape[1])]
features += [f3[:, i] for i in xrange(f3.shape[1])]
feature_names += ["F4" + r_name_for_i[i] for i in xrange(f4.shape[1])]
features += [f4[:, i] for i in xrange(f4.shape[1])]

feature_bases = [(i,) for i in xrange(len(features))]
used_bases = set(feature_bases)
feature_sizes = [f.sum() for f in features]
feature_successes = [f.multiply(results_scaled_sparse).sum() for f in features]
feature_probs = [float(feature_successes[i])/feature_sizes[i] for i in xrange(len(feature_sizes))]
feature_pvals = [lik_test(feature_sizes[i], feature_successes[i], base_success_rate) for i in xrange(len(feature_sizes))]
feature_liks = [log_lik_ratio(feature_sizes[i], feature_successes[i], base_success_rate) for i in xrange(len(feature_sizes))]

# Here begins an unprincipled interaction feature search.
# There will be false positives and false negatives, but we hope to find "enough" "good" features to be useful

min_size = n_obs/MIN_FEATURE_FRAC

iter_counter = 0
while True:
    i, j = 0, 0
    while True:
        #should we be weighting the parent features somehow?
        i, j = sorted(sample(xrange(len(features)), 2))
        prop_basis = tuple(sorted(set(feature_bases[i]+feature_bases[j])))
        if prop_basis not in used_bases: break
    iter_counter += 1
    used_bases.add(prop_basis)
    prop_name = ":".join([feature_names[f] for f in prop_basis])
    print "trying", prop_name
    prob_i = feature_probs[i]
    prob_j = feature_probs[j]
    prop_feat = features[i].multiply(features[j])
    prop_size = prop_feat.sum()
    if prop_size<min_size:
        #pretty arbitrary here
        continue
    prop_succ = prop_feat.multiply(results_scaled_sparse).sum()
    prop_prob = float(prop_succ)/prop_size
    prop_pval = max(
        lik_test(prop_size, prop_succ, prob_i),
        lik_test(prop_size, prop_succ, prob_j),
    )
    parent_lik = max(
        feature_liks[i],
        feature_liks[j],
    )
    # Base success rate is the wrong criterion here; it should be whether our marginal probs
    # are different than the parents:
    prop_lik = log_lik_ratio(prop_size, prop_succ, base_success_rate)
    
    if prop_pval>P_VAL_THRESH:
        # a greedy heuristic, statistical significance is not guaranteed.
        continue
    #Otherwise, we have a contender. Add it to the pool.
    features.append(prop_feat)
    print "including", prop_name
    feature_names.append(prop_name)
    feature_bases.append(prop_basis)
    feature_sizes.append(prop_size)
    feature_successes.append(prop_succ)
    feature_probs.append(prop_prob)
    feature_liks.append(prop_lik)
    feature_pvals.append(prop_pval)
    if len(features) >= MAX_N_FEATURES: break
    if iter_counter >= MAX_FEATURE_ITER: break

# Here's an arbitrary way of guessing the comparative importance of these:
ranks = sorted([(feature_sizes[i]*feature_liks[i], feature_bases[i], feature_names[i]) for i in xrange(len(features))])
# I could prune the least interesting half and repeat the process?

# So we go to R.
# (csr for liblineaR use, csc for R use)
mega_features = sp.sparse.hstack(features).tocsc()

# barcodes are not independent features
# we can either use them by 
# 1 combining with other bases in an outer product; or 
# 2 this post hoc hack
# Yay post hoc hack!

barcoded_features = [mega_features]
barcoded_feature_names = list(feature_names)

for i in xrange(note_barcode_sparse.shape[1]):
    prefix = "b" + str(i+1)
    barcoded_feature_names += [prefix + fname for fname in feature_names]
    barcoded_feature = mega_features.multiply(note_barcode_sparse[:,i])
    barcoded_feature.eliminate_zeros()
    barcoded_features.append(barcoded_feature)
    del(barcoded_feature)

feature_names = barcoded_feature_names
mega_features = sp.sparse.hstack(barcoded_features).tocsc()

with tables.open_file(FEATURE_TABLE_FROM_PYTHON_PATH, 'w') as table_out_handle:
    #ignore warnings for that bit; I know my column names are annoying.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        obs_table = table_out_handle.create_table('/', 'obs_meta',
            obs_table_description,
            filters=tables.Filters(complevel=5, complib='blosc'))
    for r in xrange(n_obs):
        obs_table.row['file'] = obs_meta['file'][r]
        obs_table.row['time'] = obs_meta['time'][r]
        obs_table.row['obsId'] = obs_meta['obsId'][r]
        obs_table.row['eventId'] = obs_meta['eventId'][r]
        obs_table.row['pitch'] = obs_meta['pitch'][r]
        obs_table.row['diameter'] = obs_meta['diameter'][r]
        obs_table.row['result'] = obs_meta['result'][r]
        obs_table.row['obsId'] = obs_meta['obsId'][r]
        obs_table.row['b4'] = obs_meta['barcode'][r][3]
        obs_table.row['b3'] = obs_meta['barcode'][r][2]
        obs_table.row['b2'] = obs_meta['barcode'][r][1]
        obs_table.row['b1'] = obs_meta['barcode'][r][0]
        obs_table.row.append()

    col_table = table_out_handle.create_table('/', 'col_names',
        {'i': tables.IntCol(1), 'rname': tables.StringCol(5)})
    for i in sorted(r_name_for_i.keys()):
        col_table.row['i'] = i
        col_table.row['rname'] = r_name_for_i[i]
        col_table.row.append()

    obs_table.attrs.maxAge = MAX_AGE
    obs_table.attrs.neighborhoodRadius = NEIGHBORHOOD_RADIUS

    filt = tables.Filters(complevel=5, complib='blosc')
    obs_group = table_out_handle.create_group("/", "obs")
    write_sparse_hdf(table_out_handle,
        obs_group, obs_vec,
        filt=filt)

    feature_group = table_out_handle.create_group("/", "features")
    write_sparse_hdf(table_out_handle,
        feature_group, mega_features,
        colnames=feature_names,
        filt=filt)

#Hands-free R invocation would look like this:
#> R -f sparse_linear_fit.R --args rag_from_python.h5 rag_to_python.h5
