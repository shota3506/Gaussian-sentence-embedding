import numpy as np
import argparse
import configparser
import pickle

import torch
import torchtext

import datasets
import models
from build_vocab import Vocab


config = configparser.ConfigParser()


def encode_candidates(encoder, dataloader, device):
    mean_list = []
    var_list = []
    s_ids = []
    i_list = []
    i_ids = []

    with torch.no_grad():
        for images, src_seq, src_pos, img_ids, ids in dataloader:
            images = images.to(device)
            src_seq = src_seq.to(device)
            src_pos = src_pos.to(device)
            img_embedded = images.to(torch.device("cpu"))
            mean, var = encoder(src_seq, src_pos)
            mean = mean.to(torch.device("cpu"))
            var = var.to(torch.device("cpu"))

            i_list.append(img_embedded)
            mean_list.append(mean)
            var_list.append(var)
            i_ids.append(img_ids)
            s_ids.append(ids)

    s_means = torch.cat(tuple(mean_list))
    s_vars = torch.cat(tuple(var_list))
    s_ids = torch.cat(tuple(s_ids))
    i_vectors = torch.cat(tuple(i_list))
    i_ids = torch.cat(tuple(i_ids))
    return s_means, s_vars, s_ids, i_vectors, i_ids


def remove_duplicates(s_means, s_vars, s_ids, i_vectors, i_ids):
    used_ids = set()
    mask = []
    for i, id in enumerate(i_ids):
        id = id.item()
        if id not in used_ids:
            used_ids.add(id)
            mask.append(True)
        else:
            mask.append(False)
    mask = torch.tensor(mask)

    s_means = s_means[mask]
    s_vars = s_vars[mask]
    s_ids = s_ids[mask]
    i_vectors = i_vectors[mask]
    i_ids = i_ids[mask]
    return s_means, s_vars, s_ids, i_vectors, i_ids


def get_similarity_matrix(mean, var, target, device, batch_size=10):
    target = target.to(device)
    mat = []
    with torch.no_grad():
        for i in range(0, len(mean), batch_size):
            m = mean[i:i+batch_size].to(device)
            v = var[i:i+batch_size].to(device)
            mat.append(torch.sum((m[:, None] - target[None]) ** 2 / v[:, None], dim=2))

    return torch.cat(mat).to(torch.device("cpu")).numpy()


def calc_retrieval_score(sim_mat):
    ks = [5, 10, 20]
    s2i = {"recall": {}, "precision": {}}
    i2s = {"recall": {}, "precision": {}}
    for k in ks:
        s2i["recall"][k] = 0.0
        s2i["precision"][k] = 0.0
        i2s["recall"][k] = 0.0
        i2s["precision"][k] = 0.0

    # image retrieval
    for i in range(sim_mat.shape[0]):
        ordered_ids = np.argsort(sim_mat[i])
        for k in ks:
            s2i["recall"][k] += recall_at_k(i, ordered_ids, k=k)
            # s2i["precision"][k] += precision_at_k(s_ids[n], ordered_i_ids, k=k)
    for k in ks:
        s2i["recall"][k] = s2i["recall"][k] / float(sim_mat.shape[0]) * 100
        # s2i["precision"][k] = s2i["precision"][k] / float(sim_mat.shape[0]) * 100

    # sentence retrieval
    for j in range(sim_mat.shape[1]):
        ordered_ids = np.argsort(sim_mat[:, j])
        for k in ks:
            i2s["recall"][k] += recall_at_k(j, ordered_ids, k=k)
            # i2s["precision"][k] += precision_at_k(i_ids[m], ordered_s_ids, k=k)
    for k in ks:
        i2s["recall"][k] = i2s["recall"][k] / float(sim_mat.shape[1]) * 100
        # i2s["precision"][k] = i2s["precision"][k] / float(sim_mat.shape[1]) * 100

    return s2i, i2s


def recall_at_k(gt_id, ordered_ids, k):
    TP = ordered_ids[:k].tolist().count(gt_id)
    TP_plus_FN = ordered_ids.tolist().count(gt_id)
    return float(TP) / float(TP_plus_FN)


def precision_at_k(gt_id, ordered_ids, k):
    TP = ordered_ids[:k].tolist().count(gt_id)
    return float(TP) / float(k)


def main(args):
    gpu = args.gpu
    config_path = args.config
    vocab_path = args.vocab
    img2vec_path = args.img2vec
    val_json_path = args.val_json
    sentence_encoder_path = args.sentence_encoder
    name = args.name

    print("[args] gpu=%d" % gpu)
    print("[args] config_path=%s" % config_path)
    print("[args] word2vec_path=%s" % vocab_path)
    print("[args] img2vec_path=%s" % img2vec_path)
    print("[args] val_json_path=%s" % val_json_path)
    print("[args] sentence_encoder_path=%s" % sentence_encoder_path)
    print("[args] name=%s" % name)

    device = torch.device("cuda:" + str(gpu) if torch.cuda.is_available() else "cpu")

    config.read(config_path)

    # Model parameters
    modelparams = config["modelparams"]
    sentence_encoder_name = modelparams.get("sentence_encoder")
    metric = modelparams.get("metric", "maharanobis")
    n_layers = modelparams.getint("n_layers")
    d_model = modelparams.getint("d_model")

    hyperparams = config["hyperparams"]
    batch_size = hyperparams.getint("batch_size")

    print("[info] Loading vocabulary ...")
    with open(vocab_path, 'rb') as f:
        vocab = pickle.load(f)
    dataloader_val = datasets.coco.get_loader(img2vec_path, val_json_path, vocab, batch_size)

    # Model preparation
    encoder = models.SentenceEncoder(vocab, sentence_encoder_name, d_model,
                                     n_layers, variance=(metric == "maharanobis")).to(device)
    encoder.load_state_dict(torch.load(sentence_encoder_path), strict=False)
    encoder.eval()

    # Evaluate
    print("[info] Encoding candidates ...")
    s_means, s_vars, s_ids, i_vectors, i_ids = encode_candidates(encoder, dataloader_val, device)
    s_means, s_vars, s_ids, i_vectors, i_ids = remove_duplicates(s_means, s_vars, s_ids, i_vectors, i_ids)

    print("[info] Evaluating on the validation set ...")
    sim_mat = get_similarity_matrix(s_means, s_vars, i_vectors, device)
    s2i, i2s = calc_retrieval_score(sim_mat)
    print(
        "[validation] s2i[R@5=%.02f, R@10=%.02f, R@20=%.02f], i2s[R@5=%.02f, R@10=%.02f, R@20=%.02f]" % \
        (s2i["recall"][5], s2i["recall"][10], s2i["recall"][20],
         i2s["recall"][5], i2s["recall"][10], i2s["recall"][20]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--config", type=str, required=True)
    parser.add_argument("--vocab", type=str, default=None)
    parser.add_argument("--img2vec", type=str, default=None)
    parser.add_argument("--val_json", type=str, default=None)
    parser.add_argument("--sentence_encoder", type=str, required=True)
    parser.add_argument("--name", type=str, required=True)

    args = parser.parse_args()
    main(args)
