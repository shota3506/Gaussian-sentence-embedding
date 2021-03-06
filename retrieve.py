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

    i_vectors = i_vectors[mask]
    i_ids = i_ids[mask]

    return s_means, s_vars, s_ids, i_vectors, i_ids


def main(args):
    gpu = args.gpu
    config_path = args.config
    vocab_path = args.vocab
    img2vec_path = args.img2vec
    val_json_path = args.val_json
    sentence_encoder_path = args.sentence_encoder
    name = args.name
    mode = args.mode

    print("[args] gpu=%d" % gpu)
    print("[args] config_path=%s" % config_path)
    print("[args] word2vec_path=%s" % vocab_path)
    print("[args] img2vec_path=%s" % img2vec_path)
    print("[args] val_json_path=%s" % val_json_path)
    print("[args] sentence_encoder_path=%s" % sentence_encoder_path)
    print("[args] name=%s" % name)
    print("[args] mode=%s" % mode)

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

    # Data preparation
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
    s_means = s_means.numpy()
    s_vars = s_vars.numpy()
    s_ids = s_ids.numpy()
    i_vectors = i_vectors.numpy()
    i_ids = i_ids.numpy()

    if mode == 's2i':
        print('[info] Retrieving image')
        caption_id = input("input caption id: ")
        caption_id = int(caption_id)
        coco = dataloader_val.dataset.coco
        print("Caption: %s" % coco.anns[caption_id]['caption'])
        mean = s_means[s_ids == caption_id]
        var = s_vars[s_ids == caption_id]

        scores = np.sum((mean - i_vectors) * (mean - i_vectors) / var, axis=1)
        sorted_ids = i_ids[np.argsort(scores)]
        for i in range(9):
            print(sorted_ids[i])

    elif mode == 'i2s':
        print('[info] Retrieving caption')
        image_id = input("input image id: ")
        image_id = int(image_id)
        coco = dataloader_val.dataset.coco
        target = i_vectors[i_ids == image_id]

        scores = np.sum((s_means - target) * (s_means - target) / s_vars, axis=1)
        sorted_ids = s_ids[np.argsort(scores)]
        for i in range(9):
            print("Caption: %s" % coco.anns[sorted_ids[i]]['caption'])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--config", type=str, required=True)
    parser.add_argument("--vocab", type=str, default=None)
    parser.add_argument("--img2vec", type=str, default=None)
    parser.add_argument("--val_json", type=str, default=None)
    parser.add_argument("--sentence_encoder", type=str, required=True)
    parser.add_argument("--name", type=str, required=True)
    parser.add_argument("--mode", type=str, required=True)

    args = parser.parse_args()
    main(args)
