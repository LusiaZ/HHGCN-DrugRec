import torch
import argparse
import numpy as np
import dill
import time
from torch.nn import CrossEntropyLoss
from torch.optim import Adam
import os

# os.environ["CUDA_VISIBLE_DEVICES"] = "2"

import torch.nn.functional as F
from collections import defaultdict

from models import GAMENet
from util import llprint, multi_label_metric, ddi_rate_score, get_n_params

torch.manual_seed(1203)

model_name = "GAMENet"
resume_name = "saved/GAMENet/Epoch_49_JA_0.5073_DDI_0.0858.model"

if not os.path.exists(os.path.join("saved", model_name)):
    os.makedirs(os.path.join("saved", model_name))

# Training settings
parser = argparse.ArgumentParser()
parser.add_argument("--Test", action="store_true", default=False, help="test mode")
parser.add_argument("--model_name", type=str, default=model_name, help="model name")
parser.add_argument("--resume_path", type=str, default=resume_name, help="resume path")
parser.add_argument("--ddi", action="store_true", default=True, help="using ddi")
parser.add_argument("--lr", type=float, default=1e-4, help="learning rate")
parser.add_argument("--target_ddi", type=float, default=0.06, help="target ddi")
parser.add_argument("--T", type=float, default=2.0, help="T")
parser.add_argument("--decay_weight", type=float, default=0.85, help="decay weight")
parser.add_argument("--dim", type=int, default=64, help="dimension")
parser.add_argument("--cuda", type=int, default=0, help="which cuda")

args = parser.parse_args()
def hit_and_ndcg(y_true, y_score, k=5):
    hit_ratio = []
    ndcg = []

    for yt, ys in zip(y_true, y_score):
        indices = np.argsort(ys)[::-1][:k]  # 取前k个预测分数最高的药物
        top_k_preds = set(indices)
        actual = set(np.where(yt == 1)[0])

        # Hit Ratio
        hit = len(actual & top_k_preds) > 0
        hit_ratio.append(hit)

        # NDCG
        dcg = sum([1.0 / np.log2(i + 2) for i in range(len(indices)) if indices[i] in actual])
        idcg = sum([1.0 / np.log2(i + 2) for i in range(len(actual))])
        ndcg.append(dcg / idcg if idcg > 0 else 0.0)

    return np.mean(hit_ratio), np.mean(ndcg)

# evaluate
def eval(model, data_eval, voc_size, epoch):
    model.eval()

    smm_record = []
    ja, prauc, avg_p, avg_r, avg_f1 = [[] for _ in range(5)]
    hit_ratio_1, hit_ratio_2, hit_ratio_3, hit_ratio_4, hit_ratio_5 = [[] for _ in range(5)]
    ndcg_1, ndcg_2, ndcg_3, ndcg_4, ndcg_5 = [[] for _ in range(5)]
    med_cnt, visit_cnt = 0, 0

    for step, input in enumerate(data_eval):
        y_gt, y_pred, y_pred_prob, y_pred_label = [], [], [], []

        for adm_idx, adm in enumerate(input):
            target_output = model(input[: adm_idx + 1])

            y_gt_tmp = np.zeros(voc_size[2])
            y_gt_tmp[adm[2]] = 1
            y_gt.append(y_gt_tmp)

            # prediction prod
            target_output = F.sigmoid(target_output).detach().cpu().numpy()[0]
            y_pred_prob.append(target_output)

            # predioction med set
            y_pred_tmp = target_output.copy()
            y_pred_tmp[y_pred_tmp >= 0.5] = 1
            y_pred_tmp[y_pred_tmp < 0.5] = 0
            y_pred.append(y_pred_tmp)

            # prediction label
            y_pred_label_tmp = np.where(y_pred_tmp == 1)[0]
            y_pred_label.append(sorted(y_pred_label_tmp))
            visit_cnt += 1
            med_cnt += len(y_pred_label_tmp)

        smm_record.append(y_pred_label)
        adm_ja, adm_prauc, adm_avg_p, adm_avg_r, adm_avg_f1 = multi_label_metric(
            np.array(y_gt), np.array(y_pred), np.array(y_pred_prob)
        )

        ja.append(adm_ja)
        prauc.append(adm_prauc)
        avg_p.append(adm_avg_p)
        avg_r.append(adm_avg_r)
        avg_f1.append(adm_avg_f1)

        hit_1, ndcg_1_value = hit_and_ndcg(y_gt, y_pred_prob, k=1)
        hit_ratio_1.append(hit_1)
        ndcg_1.append(ndcg_1_value)

        hit_2, ndcg_2_value = hit_and_ndcg(y_gt, y_pred_prob, k=2)
        hit_ratio_2.append(hit_2)
        ndcg_2.append(ndcg_2_value)

        hit_3, ndcg_3_value = hit_and_ndcg(y_gt, y_pred_prob, k=3)
        hit_ratio_3.append(hit_3)
        ndcg_3.append(ndcg_3_value)

        hit_4, ndcg_4_value = hit_and_ndcg(y_gt, y_pred_prob, k=4)
        hit_ratio_4.append(hit_4)
        ndcg_4.append(ndcg_4_value)

        hit_5, ndcg_5_value = hit_and_ndcg(y_gt, y_pred_prob, k=5)
        hit_ratio_5.append(hit_5)
        ndcg_5.append(ndcg_5_value)

        llprint("\rtest step: {} / {}".format(step, len(data_eval)))

    # ddi rate
    ddi_rate = ddi_rate_score(smm_record, path="../data/output/ddi_A_final.pkl")

    llprint(
        "\nDDI Rate: {:.4f}, Jaccard: {:.4f}, PRAUC: {:.4f}, AVG_PRC: {:.4f}, AVG_RECALL: {:.4f}, AVG_F1: {:.4f}, AVG_MED: {:.4f}, HR@1: {:.4f}, HR@2: {:.4f}, HR@3: {:.4f}, HR@4: {:.4f}, HR@5: {:.4f}, NDCG@1: {:.4f}, NDCG@2: {:.4f}, NDCG@3: {:.4f}, NDCG@4: {:.4f}, NDCG@5: {:.4f}\n".format(
            float(ddi_rate), float(np.mean(ja)), float(np.mean(prauc)), float(np.mean(avg_p)), float(np.mean(avg_r)),
            float(np.mean(avg_f1)),
            float(med_cnt) / float(visit_cnt), float(np.mean(hit_ratio_1)), float(np.mean(hit_ratio_2)),
            float(np.mean(hit_ratio_3)),
            float(np.mean(hit_ratio_4)), float(np.mean(hit_ratio_5)), float(np.mean(ndcg_1)), float(np.mean(ndcg_2)),
            float(np.mean(ndcg_3)),
            float(np.mean(ndcg_4)), float(np.mean(ndcg_5))
        )
    )

    return (
        ddi_rate,
        np.mean(ja),
        np.mean(prauc),
        np.mean(avg_p),
        np.mean(avg_r),
        np.mean(avg_f1),
        med_cnt / visit_cnt,
        np.mean(hit_ratio_1),
        np.mean(hit_ratio_2),
        np.mean(hit_ratio_3),
        np.mean(hit_ratio_4),
        np.mean(hit_ratio_5),
        np.mean(ndcg_1),
        np.mean(ndcg_2),
        np.mean(ndcg_3),
        np.mean(ndcg_4),
        np.mean(ndcg_5)
    )


def main():

    data_path = "../data/output/records_final.pkl"
    voc_path = "../data/output/voc_final.pkl"

    ehr_adj_path = "../data/output/ehr_adj_final.pkl"
    ddi_adj_path = "../data/output/ddi_A_final.pkl"
    device = torch.device("cuda:{}".format(args.cuda))

    ehr_adj = dill.load(open(ehr_adj_path, "rb"))
    ddi_adj = dill.load(open(ddi_adj_path, "rb"))
    data = dill.load(open(data_path, "rb"))

    voc = dill.load(open(voc_path, "rb"))
    diag_voc, pro_voc, med_voc = voc["diag_voc"], voc["pro_voc"], voc["med_voc"]

    # np.random.seed(2048)
    # np.random.shuffle(data)
    split_point = int(len(data) * 2 / 3)
    data_train = data[:split_point]
    eval_len = int(len(data[split_point:]) / 2)
    data_test = data[split_point : split_point + eval_len]
    data_eval = data[split_point + eval_len :]

    voc_size = (len(diag_voc.idx2word), len(pro_voc.idx2word), len(med_voc.idx2word))
    model = GAMENet(
        voc_size,
        ehr_adj,
        ddi_adj,
        emb_dim=args.dim,
        device=device,
        ddi_in_memory=args.ddi,
    )
    # model.load_state_dict(torch.load(open(args.resume_path, 'rb')))

    if args.Test:
        model.load_state_dict(torch.load(open(args.resume_path, "rb")))
        model.to(device=device)
        tic = time.time()
        result = []
        for _ in range(10):
            test_sample = np.random.choice(
                data_test, round(len(data_test) * 0.8), replace=True
            )
            ddi_rate, ja, prauc, avg_p, avg_r, avg_f1, avg_med, hit_ratio_1, hit_ratio_2, hit_ratio_3, hit_ratio_4, hit_ratio_5, ndcg_1, ndcg_2, ndcg_3, ndcg_4, ndcg_5 = eval(
                model, test_sample, voc_size, 0)
            result.append([
                float(ddi_rate), float(ja), float(avg_f1), float(prauc), float(avg_med),
                float(hit_ratio_1), float(hit_ratio_2), float(hit_ratio_3), float(hit_ratio_4), float(hit_ratio_5),
                float(ndcg_1), float(ndcg_2), float(ndcg_3), float(ndcg_4), float(ndcg_5)
            ])

        result = np.array(result)
        mean = result.mean(axis=0)
        std = result.std(axis=0)

        outstring = ""
        for m, s in zip(mean, std):
            outstring += "{:.4f} $\pm$ {:.4f} & ".format(m, s)

        print(outstring)
        print("test time: {}".format(time.time() - tic))
        return

    model.to(device=device)
    print("parameters", get_n_params(model))
    optimizer = Adam(list(model.parameters()), lr=args.lr)

    history = defaultdict(list)
    best_epoch, best_ja = 0, 0

    EPOCH = 50
    for epoch in range(EPOCH):
        tic = time.time()
        print("\nepoch {} --------------------------".format(epoch + 1))
        prediction_loss_cnt, neg_loss_cnt = 0, 0
        model.train()
        for step, input in enumerate(data_train):
            for idx, adm in enumerate(input):
                seq_input = input[: idx + 1]
                loss_bce_target = np.zeros((1, voc_size[2]))
                loss_bce_target[:, adm[2]] = 1

                loss_multi_target = np.full((1, voc_size[2]), -1)
                for idx, item in enumerate(adm[2]):
                    loss_multi_target[0][idx] = item

                target_output1, loss_ddi = model(seq_input)

                loss_bce = F.binary_cross_entropy_with_logits(
                    target_output1, torch.FloatTensor(loss_bce_target).to(device)
                )
                loss_multi = F.multilabel_margin_loss(
                    F.sigmoid(target_output1),
                    torch.LongTensor(loss_multi_target).to(device),
                )
                if args.ddi:
                    target_output1 = F.sigmoid(target_output1).detach().cpu().numpy()[0]
                    target_output1[target_output1 >= 0.5] = 1
                    target_output1[target_output1 < 0.5] = 0
                    y_label = np.where(target_output1 == 1)[0]
                    current_ddi_rate = ddi_rate_score(
                        [[y_label]], path="../data/output/ddi_A_final.pkl"
                    )
                    if current_ddi_rate <= args.target_ddi:
                        loss = 0.9 * loss_bce + 0.1 * loss_multi
                        prediction_loss_cnt += 1
                    else:
                        rnd = np.exp((args.target_ddi - current_ddi_rate) / args.T)
                        if np.random.rand(1) < rnd:
                            loss = loss_ddi
                            neg_loss_cnt += 1
                        else:
                            loss = 0.9 * loss_bce + 0.1 * loss_multi
                            prediction_loss_cnt += 1
                else:
                    loss = 0.9 * loss_bce + 0.1 * loss_multi

                optimizer.zero_grad()
                loss.backward(retain_graph=True)
                optimizer.step()

            llprint("\rtraining step: {} / {}".format(step, len(data_train)))

        args.T *= args.decay_weight

        print()
        tic2 = time.time()
        ddi_rate, ja, prauc, avg_p, avg_r, avg_f1, avg_med, hit_ratio_1, hit_ratio_2, hit_ratio_3, hit_ratio_4, hit_ratio_5, ndcg_1, ndcg_2, ndcg_3, ndcg_4, ndcg_5 = eval(
            model, data_eval, voc_size, epoch
        )
        print(
            "training time: {}, test time: {}".format(
                time.time() - tic, time.time() - tic2
            )
        )

        history["ja"].append(ja)
        history["ddi_rate"].append(ddi_rate)
        history["avg_p"].append(avg_p)
        history["avg_r"].append(avg_r)
        history["avg_f1"].append(avg_f1)
        history["prauc"].append(prauc)
        history["med"].append(avg_med)
        history["hit_ratio_1"].append(hit_ratio_1)
        history["hit_ratio_2"].append(hit_ratio_2)
        history["hit_ratio_3"].append(hit_ratio_3)
        history["hit_ratio_4"].append(hit_ratio_4)
        history["hit_ratio_5"].append(hit_ratio_5)
        history["ndcg_1"].append(ndcg_1)
        history["ndcg_2"].append(ndcg_2)
        history["ndcg_3"].append(ndcg_3)
        history["ndcg_4"].append(ndcg_4)
        history["ndcg_5"].append(ndcg_5)

        if epoch >= 5:
            print(
                "ddi: {}, Med: {}, Ja: {}, F1: {}, PRAUC: {}, HR@1: {}, HR@2: {}, HR@3: {}, HR@4: {}, HR@5: {}, NDCG@1: {}, NDCG@2: {}, NDCG@3: {}, NDCG@4: {}, NDCG@5: {}".format(
                    np.mean(history["ddi_rate"][-5:]),
                    np.mean(history["med"][-5:]),
                    np.mean(history["ja"][-5:]),
                    np.mean(history["avg_f1"][-5:]),
                    np.mean(history["prauc"][-5:]),
                    np.mean(history["hit_ratio_1"][-5:]),
                    np.mean(history["hit_ratio_2"][-5:]),
                    np.mean(history["hit_ratio_3"][-5:]),
                    np.mean(history["hit_ratio_4"][-5:]),
                    np.mean(history["hit_ratio_5"][-5:]),
                    np.mean(history["ndcg_1"][-5:]),
                    np.mean(history["ndcg_2"][-5:]),
                    np.mean(history["ndcg_3"][-5:]),
                    np.mean(history["ndcg_4"][-5:]),
                    np.mean(history["ndcg_5"][-5:]),
                )
            )

        torch.save(
            model.state_dict(),
            open(
                os.path.join(
                    "saved",
                    args.model_name,
                    "Epoch_{}_JA_{:.4f}_DDI_{:.4f}.model".format(epoch, ja, ddi_rate),
                ),
                "wb",
            ),
        )

        if epoch != 0 and best_ja < ja:
            best_epoch = epoch
            best_ja = ja

        print("best_epoch: {}".format(best_epoch))

    dill.dump(
        history,
        open(
            os.path.join(
                "saved", args.model_name, "history_{}.pkl".format(args.model_name)
            ),
            "wb",
        ),
    )


if __name__ == "__main__":
    main()
