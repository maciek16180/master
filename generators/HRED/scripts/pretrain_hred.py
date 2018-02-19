from __future__ import print_function

import sys
import time
import argparse
import os


parser = argparse.ArgumentParser(description='Pre-train script for HRED.')
parser.add_argument('-mt', '--mt_path', default='data/mtriples')
parser.add_argument('-o', '--output_dir', default='output')
parser.add_argument('-bs', '--batch_size', default=30, type=int)
parser.add_argument('-e', '--num_epochs', default=4, type=int)
parser.add_argument('-s', '--samples', default=200, type=int)
parser.add_argument('-li', '--log_interval', default=5000, type=int)
parser.add_argument('-m', '--mode', choices=['ssoft', 'full'], default='ssoft')
parser.add_argument('-lr', '--learning_rate', default=0.0002, type=float)


args = parser.parse_args()

# set paths
if not os.path.exists(args.output_dir):
    os.makedirs(args.output_dir)
elif os.listdir(args.output_dir):
    sys.exit(
        "Chosen output directory already exists and is not empty. Aborting.")

# redirect all prints to log file
log_path = os.path.join(args.output_dir, 'log')
print("All prints are redirected to", log_path)
log = open(log_path, 'w', buffering=1)
sys.stderr = log
sys.stdout = log

sys.path.append('../../')
from HRED import HRED
from data_load.mt_load import load_mt, get_mt_voc, get_w2v_embs
from data_load.subtle_load import load_subtle

print("\nRun params:")
for arg in vars(args):
    print(arg.ljust(25), getattr(args, arg))
print("\n")

################################

print("Loading data...")

_, valid, _ = load_mt(path=args.mt_path, split=True)
_, _, voc_size, freqs = get_mt_voc(path=args.mt_path)
subtle_data = load_subtle(path=args.mt_path, split=True)

net = HRED(voc_size=voc_size,
           emb_size=300,
           lv1_rec_size=300,
           lv2_rec_size=300,
           out_emb_size=300,
           num_sampled=args.samples,
           ssoft_probs=freqs,
           mode=args.mode,
           learning_rate=args.learning_rate,
           skip_gen=True)


model_filename = os.path.join(args.output_dir, 'model')

t0 = time.time()
for epoch in range(1, args.num_epochs + 1):
    print('\n\nStarting epoch {}...\n'.format(epoch))
    train_error = net.train_one_epoch(
        train_data=subtle_data,
        batch_size=args.batch_size,
        log_interval=args.log_interval)
    val_error = net.validate(
        val_data=valid,
        batch_size=args.batch_size)
    print('\nTraining loss:   {}'.format(train_error))
    print('MT validation loss: {}'.format(val_error))
    net.save_params(model_filename + '.ep{:02d}'.format(epoch))

print('\n\nTotal training time: {:.2f}s'.format(time.time() - t0))
