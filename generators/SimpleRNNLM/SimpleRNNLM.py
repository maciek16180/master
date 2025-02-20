from __future__ import print_function

import numpy as np
import theano
import theano.tensor as T
import time

import lasagne as L

import sys
sys.path.append('../')

from layers import SampledSoftmaxDenseLayer
from layers import ShiftLayer
from layers import TrainPartOfEmbsLayer

print('floatX ==', theano.config.floatX)
print('device ==', theano.config.device)


class SimpleRNNLM(object):

    def __init__(self, voc_size, emb_size, rec_size, train_emb=True,
                 train_inds=[], mode='ssoft', learning_rate=0.0002, **kwargs):

        self.voc_size = voc_size
        self.emb_size = emb_size
        self.rec_size = rec_size

        self.train_inds = train_inds
        self.train_emb = train_emb
        self.learning_rate = learning_rate
        self.mode = mode

        self.input_var = T.imatrix('inputs')
        self.mask_input_var = T.matrix('input_mask')
        mask_idx = self.mask_input_var.nonzero()

        self.emb_init = kwargs.get('emb_init', None)

        self.generator_init = T.matrix('generator_init')

        # BUILD THE MODEL
        print('Building the model...')

        assert mode in ['ssoft', 'full']

        self.train_net = self._build_net(train_emb=self.train_emb, **kwargs)

        skip_train = kwargs.get('skip_train', False)
        skip_gen = kwargs.get('skip_gen', False)

        if not skip_train:

            # CALCULATE THE LOSS

            train_out = L.layers.get_output(self.train_net)
            test_out = L.layers.get_output(self.train_net, deterministic=True)

            train_loss = -T.log(train_out[mask_idx]).mean()
            test_loss = -T.log(test_out[mask_idx]).mean()

            # MAKE TRAIN AND VALIDATION FUNCTIONS
            print('Compiling theano functions...')

            params = L.layers.get_all_params(self.train_net, trainable=True)

            if kwargs.has_key('update_fn'):
                update_fn = kwargs['update_fn']
            else:
                def update_fn(l, p): return L.updates.adam(
                    l, p, learning_rate=self.learning_rate)

            updates = update_fn(train_loss, params)

            self.train_fn = theano.function(
                [self.input_var, self.mask_input_var],
                train_loss, updates=updates)
            self.val_fn = theano.function(
                [self.input_var, self.mask_input_var], test_loss)
        else:
            print('Skipping training part...')

        if not skip_gen:
            # BUILD NET FOR GENERATING, WITH SHARED PARAMETERS
            print('Building a network for generating...')

            all_params = {x.name: x
                          for x in L.layers.get_all_params(self.train_net)}

            self.gen_net = self._build_net_with_params(all_params)

            gen_net_out = L.layers.get_output(self.gen_net, deterministic=True)
            self.get_probs_and_new_dec_init_fn = theano.function(
                [self.input_var, self.generator_init], gen_net_out)
        else:
            print('Skipping generating part...')

        print('Done')

    def train_one_epoch(self, train_data, batch_size, log_interval=10):
        train_err = 0.
        train_batches = 0
        num_training_words = 0
        start_time = time.time()

        for batch in self.iterate_minibatches(train_data, batch_size,
                                              shuffle=True):
            inputs, mask = batch

            num_batch_words = mask.sum()
            train_err += self.train_fn(inputs, mask) * num_batch_words
            train_batches += 1
            num_training_words += num_batch_words

            if not train_batches % log_interval:
                print("Done {} batches in {:.2f}s\ttraining loss:\t{:.6f}".
                      format(train_batches, time.time() - start_time,
                             train_err / num_training_words))

        return train_err / num_training_words

    def validate(self, val_data, batch_size):
        val_err = 0.
        val_batches = 0
        num_validate_words = 0
        start_time = time.time()

        for batch in self.iterate_minibatches(val_data, batch_size):
            inputs, mask = batch

            num_batch_words = mask.sum()
            val_err += self.val_fn(inputs, mask) * num_batch_words
            val_batches += 1
            num_validate_words += num_batch_words

            if not val_batches % 100:
                print("Done {} batches in {:.2f}s".format(
                    val_batches, time.time() - start_time))

        return val_err / num_validate_words

    def save_params(self, fname):
        np.savez(fname, *L.layers.get_all_param_values(self.train_net))

    def load_params(self, fname):
        with np.load(fname) as f:
            param_values = [f['arr_%d' % i] for i in range(len(f.files))]
            L.layers.set_all_param_values(self.train_net, param_values)

    def _build_net(self, num_sampled, train_emb=True, ssoft_probs=None,
                   sample_unique=False, **kwargs):
        l_in = L.layers.InputLayer(shape=(None, None),
                                   input_var=self.input_var)

        l_mask = L.layers.InputLayer(shape=(None, None),
                                     input_var=self.mask_input_var)

        if train_emb:
            if self.emb_init is None:
                l_emb = L.layers.EmbeddingLayer(l_in,
                                                input_size=self.voc_size,
                                                output_size=self.emb_size,
                                                name='emb')
            else:
                l_emb = L.layers.EmbeddingLayer(l_in,
                                                input_size=self.voc_size,
                                                output_size=self.emb_size,
                                                W=self.emb_init,
                                                name='emb')
        else:
            if self.emb_init is not None:
                l_emb = TrainPartOfEmbsLayer(l_in,
                                             output_size=self.emb_size,
                                             input_size=self.voc_size,
                                             W=self.emb_init[self.train_inds],
                                             E=self.emb_init,
                                             train_inds=self.train_inds,
                                             name='emb')
            else:
                l_emb = TrainPartOfEmbsLayer(l_in,
                                             output_size=self.emb_size,
                                             input_size=self.voc_size,
                                             train_inds=self.train_inds,
                                             name='emb')

        l_gru = L.layers.GRULayer(l_emb,
                                  num_units=self.rec_size,
                                  grad_clipping=100,
                                  mask_input=l_mask,
                                  name='GRU')

        l_resh = L.layers.ReshapeLayer(ShiftLayer(l_gru),
                                       shape=(-1, self.rec_size))

        l_ssoft = SampledSoftmaxDenseLayer(l_resh, num_sampled, self.voc_size,
                                           targets=self.input_var.ravel(),
                                           probs=ssoft_probs,
                                           sample_unique=sample_unique,
                                           mode=self.mode,
                                           name='soft')

        l_out = L.layers.ReshapeLayer(l_ssoft, tuple(self.input_var.shape))

        return l_out

    def _build_net_with_params(self, params):

        l_in = L.layers.InputLayer(shape=(None, None),
                                   input_var=self.input_var)

        l_emb = L.layers.EmbeddingLayer(l_in,
                                        input_size=self.voc_size,
                                        output_size=self.emb_size,
                                        W=params['emb.W'])

        l_gen_init = L.layers.InputLayer(shape=(None, self.rec_size),
                                         input_var=self.generator_init)

        l_gru = L.layers.GRULayer(
            l_emb,
            num_units=self.rec_size,
            hid_init=l_gen_init,
            grad_clipping=100,
            only_return_final=True,
            resetgate=L.layers.Gate(
                W_in=params['GRU.W_in_to_resetgate'],
                W_hid=params['GRU.W_hid_to_resetgate'],
                W_cell=None,
                b=params['GRU.b_resetgate']),
            updategate=L.layers.Gate(
                W_in=params['GRU.W_in_to_updategate'],
                W_hid=params['GRU.W_hid_to_updategate'],
                W_cell=None,
                b=params['GRU.b_updategate']),
            hidden_update=L.layers.Gate(
                W_in=params['GRU.W_in_to_hidden_update'],
                W_hid=params['GRU.W_hid_to_hidden_update'],
                W_cell=None,
                b=params['GRU.b_hidden_update'],
                nonlinearity=L.nonlinearities.tanh))

        l_soft = L.layers.DenseLayer(l_gru,
                                     num_units=self.voc_size,
                                     nonlinearity=L.nonlinearities.softmax,
                                     W=params['soft.W'],
                                     b=params['soft.b'])

        return l_soft, l_gru

    def iterate_minibatches(self, inputs, batch_size, pad=-1, shuffle=False):
        if shuffle:
            indices = np.arange(len(inputs))
            np.random.shuffle(indices)
            inputs = np.array(inputs)

        for start_idx in range(0, len(inputs) - batch_size + 1, batch_size):
            if shuffle:
                excerpt = indices[start_idx:start_idx + batch_size]
            else:
                excerpt = slice(start_idx, start_idx + batch_size)

            inp = inputs[excerpt]

            inp_max_len = max(map(len, inp))
            inp = map(lambda l: l + [pad] * (inp_max_len - len(l)), inp)
            inp = np.asarray(inp, dtype=np.int32)
            mask = (inp != pad).astype(np.float32)

            yield inp, mask
