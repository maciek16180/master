import numpy as np
from itertools import chain


def split_utt(utt):
    u1, u2, u3 = [i for i, j in enumerate(utt) if j == 1]
    return [utt[:u2], utt[u2:u3], utt[u3:]]


def load_mt(path, split=False, trim=200, threeD=True):
    tr = np.load(path + 'Training.triples.pkl')
    vl = np.load(path + 'Validation.triples.pkl')
    ts = np.load(path + 'Test.triples.pkl')

    if split:
        tr = list(chain(*map(split_utt, tr)))
        vl = list(chain(*map(split_utt, vl)))
        ts = list(chain(*map(split_utt, ts)))

    if trim is not None:
        if not split:
            tr = [utt for utt in tr if len(utt) <= trim]
            vl = [utt for utt in vl if len(utt) <= trim]
            ts = [utt for utt in ts if len(utt) <= trim]
        else:
            inds_to_remove = [set(), set(), set()]
            for l in range(3):
                data = [tr, vl, ts][l]
                for k in range(len(data)):
                    if len(data[k]) > trim:
                        for i in range(3):
                            inds_to_remove[l].add(k - (k % 3) + i)

            tr = [tr[i] for i in range(len(tr)) if i not in inds_to_remove[0]]
            vl = [vl[i] for i in range(len(vl)) if i not in inds_to_remove[1]]
            ts = [ts[i] for i in range(len(ts)) if i not in inds_to_remove[2]]

    if threeD:
        tr = [tr[i:i+3] for i in range(0, len(tr), 3)]
        vl = [vl[i:i+3] for i in range(0, len(vl), 3)]
        ts = [ts[i:i+3] for i in range(0, len(ts), 3)]

    return tr, vl, ts


def get_mt_voc(path):
    word_list = np.load(path + 'Training.dict.pkl')
    word_list.sort(key=lambda x: x[1])
    freqs = np.array([x[2] for x in word_list])
    total_count = float(freqs.sum())

    words = [x[:2] for x in word_list]

    w_to_idx = dict(words)
    idx_to_w = sorted(w_to_idx, key=lambda w: w_to_idx[w])

    return idx_to_w, w_to_idx, len(w_to_idx), freqs / total_count


def get_w2v_embs(path):
    word2vec_embs, word2vec_embs_mask = np.load(path + 'Word2Vec_WordEmb.pkl')
    word2vec_embs = word2vec_embs.astype(np.float32)
    return word2vec_embs, word2vec_embs_mask
