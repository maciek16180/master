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
from layers import L2PoolingLayer
from layers import TrainPartOfEmbsLayer

print('floatX ==', theano.config.floatX)
print('device ==', theano.config.device)


class HRED():

    def __init__(self, voc_size, emb_size, lv1_rec_size, lv2_rec_size,
                 out_emb_size, train_emb=True, train_inds=[], mode='ssoft',
                 learning_rate=.0002, **kwargs):

        self.voc_size = voc_size
        self.emb_size = emb_size
        self.lv1_rec_size = lv1_rec_size
        self.lv2_rec_size = lv2_rec_size
        self.out_emb_size = out_emb_size

        self.train_inds = train_inds
        self.train_emb = train_emb
        self.learning_rate = learning_rate
        self.mode = mode

        self.input_gen_var = T.imatrix('inputs_gen')
        self.input_var = T.itensor3('inputs')
        self.mask_input_var = T.tensor3('input_mask')
        mask_idx = self.mask_input_var.nonzero()

        self.emb_init = kwargs.get('emb_init', None)

        self.context_init = T.matrix('context_init')
        self.decoder_init = T.matrix('decoder_init')

        assert mode in ['ssoft', 'full']

        skip_train = kwargs.get('skip_train', False)
        skip_gen = kwargs.get('skip_gen', False)

        # BUILD THE MODEL
        print('Building the model...')

        self.train_net = self._build_hred(train_emb=self.train_emb, **kwargs)

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
                 update_fn = lambda l, p: L.updates.adam(
                    l, p, learning_rate=self.learning_rate)

            updates = update_fn(train_loss, params)

            print('    train_fn...')
            self.train_fn = theano.function(
                [self.input_var, self.mask_input_var],
                train_loss, updates=updates)

            print('    val_fn...')
            self.val_fn = theano.function(
                [self.input_var, self.mask_input_var],
                test_loss)
        else:
            print('Skipping training part...')

        if not skip_gen:

            # BUILD NET FOR GENERATING, WITH SHARED PARAMETERS
            print('Building a network for generating...')

            all_params = {x.name: x
                          for x in L.layers.get_all_params(self.train_net)}

            self.context_net = self._build_context_net_with_params(all_params)
            self.decoder_net = self._build_decoder_net_with_params(all_params)

            dec_net_out = L.layers.get_output(self.decoder_net,
                                              deterministic=True)
            new_con_init = L.layers.get_output(self.context_net,
                                               deterministic=True)

            print('    get_probs_and_new_dec_init_fn...')
            self.get_probs_and_new_dec_init_fn = theano.function(
                [self.input_gen_var, self.decoder_init], dec_net_out)

            print('    get_new_con_init_fn...')
            self.get_new_con_init_fn = theano.function(
                [self.input_gen_var, self.context_init], new_con_init)
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

    def validate(self, val_data, batch_size, log_interval=100):
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

            if not val_batches % log_interval:
                print("Done {} batches in {:.2f}s".format(
                    val_batches, time.time() - start_time))

        return val_err / num_validate_words

    def save_params(self, fname):
        np.savez(fname, *L.layers.get_all_param_values(self.train_net))

    def load_params(self, fname, E=None):
        with np.load(fname) as f:
            param_values = [f['arr_%d' % i] for i in range(len(f.files))]
            L.layers.set_all_param_values(self.train_net, param_values)

    # DONE: make it so we don't have to rebuild the net to feed in
    #       context with different n.
    # NOTE: n has to remain constant throughout the batch (different batches
    # can have different ns though)

    # train_emb=False means we don't train ALL embeddings, but we can still
    # train a few vectors specified in train_inds

    def _build_hred(self, num_sampled, ssoft_probs=None, train_emb=True,
                    emb_dropout=False, **kwargs):

        if train_emb and self.train_inds:
            print('train_inds has no effect if all embeddings are learned!')

        batch_size, n, sequence_len = self.input_var.shape

        ''' Inputs '''

        l_in = L.layers.InputLayer(shape=(None, None, None),
                                   input_var=self.input_var)
        l_in = L.layers.reshape(l_in, (batch_size * n, sequence_len))

        l_mask = L.layers.InputLayer(shape=(None, None, None),
                                     input_var=self.mask_input_var)
        l_mask = L.layers.reshape(l_mask, (batch_size * n, sequence_len))

        ''' Word embeddings '''

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

        if emb_dropout:
            print('Using dropout.')
            l_emb = L.layers.dropout(l_emb)

        ''' Level 1 (sentence) BiGRU encoding with L2-pooling '''

        l_lv1_enc_forw = L.layers.GRULayer(l_emb,
                                           num_units=self.lv1_rec_size,
                                           grad_clipping=100,
                                           mask_input=l_mask,
                                           name='GRU1forw')

        l_lv1_enc_back = L.layers.GRULayer(l_emb,
                                           num_units=self.lv1_rec_size,
                                           grad_clipping=100,
                                           mask_input=l_mask,
                                           backwards=True,
                                           name='GRU1back')

        l2_pooled_forw = L2PoolingLayer(l_lv1_enc_forw, l_mask)
        l2_pooled_back = L2PoolingLayer(l_lv1_enc_back, l_mask)

        # concatenation of L2-pooled states
        l_lv1_enc = L.layers.ConcatLayer(
            [l2_pooled_forw, l2_pooled_back], axis=1)

        ''' Level 2 (context) encoding '''

        l_resh = L.layers.ReshapeLayer(l_lv1_enc, (
            batch_size, n, 2 * self.lv1_rec_size))

        # batch_size x n x lv2_rec_size
        l_lv2_enc = L.layers.GRULayer(l_resh,
                                      num_units=self.lv2_rec_size,
                                      grad_clipping=100,
                                      name='GRU2')

        ''' Decoder '''

        # we want to use i-th utterance summary as an init for decoding (i+1)th
        l_shift = ShiftLayer(l_lv2_enc)

        l_resh2 = L.layers.ReshapeLayer(l_shift, (
            batch_size * n, self.lv2_rec_size))

        # batch_size * n x lv1_rec_size
        l_dec_inits = L.layers.DenseLayer(l_resh2,
                                          num_units=self.lv1_rec_size,
                                          nonlinearity=L.nonlinearities.tanh,
                                          name='dec_init')

        # batch_size * n x sequence_len x lv1_rec_size
        l_dec = L.layers.GRULayer(l_emb,
                                  num_units=self.lv1_rec_size,
                                  grad_clipping=100,
                                  mask_input=l_mask,
                                  hid_init=l_dec_inits,
                                  name='GRUdec')

        l_resh3 = L.layers.ReshapeLayer(ShiftLayer(l_dec, pad=l_dec_inits), (
            batch_size * n * sequence_len, self.lv1_rec_size))

        l_Ho = L.layers.DenseLayer(l_resh3,
                                   num_units=self.out_emb_size,
                                   nonlinearity=None,
                                   name='ho')

        l_resh4 = L.layers.ReshapeLayer(ShiftLayer(l_emb), (
            batch_size * n * sequence_len, self.emb_size))

        l_Eo = L.layers.DenseLayer(l_resh4,
                                   num_units=self.out_emb_size,
                                   b=None,
                                   nonlinearity=None,
                                   name='eo')

        l_soft_in = L.layers.ElemwiseSumLayer([l_Ho, l_Eo])

        l_ssoft = SampledSoftmaxDenseLayer(l_soft_in,
                                           num_sampled,
                                           self.voc_size,
                                           targets=self.input_var.ravel(),
                                           probs=ssoft_probs,
                                           sample_unique=False,
                                           mode=self.mode,
                                           name='soft')

        l_out = L.layers.ReshapeLayer(l_ssoft, (batch_size, n, sequence_len))

        return l_out

    def _build_context_net_with_params(self, params):

        l_in = L.layers.InputLayer(shape=(1, None),
                                   input_var=self.input_gen_var)

        if self.train_emb:
            l_emb = L.layers.EmbeddingLayer(l_in,
                                            input_size=self.voc_size,
                                            output_size=self.emb_size,
                                            W=params['emb.W'])
        else:
            W_init = params['emb.W'] if self.train_inds else None
            l_emb = TrainPartOfEmbsLayer(l_in,
                                         output_size=self.emb_size,
                                         input_size=self.voc_size,
                                         W=W_init,
                                         E=params['emb.E'],
                                         train_inds=self.train_inds)

        l_lv1_enc_forw = L.layers.GRULayer(
            l_emb,
            num_units=self.lv1_rec_size,
            grad_clipping=100,
            resetgate=L.layers.Gate(
                W_in=params['GRU1forw.W_in_to_resetgate'],
                W_hid=params['GRU1forw.W_hid_to_resetgate'],
                W_cell=None,
                b=params['GRU1forw.b_resetgate']),
            updategate=L.layers.Gate(
                W_in=params['GRU1forw.W_in_to_updategate'],
                W_hid=params['GRU1forw.W_hid_to_updategate'],
                W_cell=None,
                b=params['GRU1forw.b_updategate']),
            hidden_update=L.layers.Gate(
                W_in=params['GRU1forw.W_in_to_hidden_update'],
                W_hid=params['GRU1forw.W_hid_to_hidden_update'],
                W_cell=None,
                b=params['GRU1forw.b_hidden_update'],
                nonlinearity=L.nonlinearities.tanh),
            hid_init=params['GRU1forw.hid_init'])

        # backward pass of encoder rnn
        l_lv1_enc_back = L.layers.GRULayer(
            l_emb,
            num_units=self.lv1_rec_size,
            grad_clipping=100,
            backwards=True,
            resetgate=L.layers.Gate(
                W_in=params['GRU1back.W_in_to_resetgate'],
                W_hid=params['GRU1back.W_hid_to_resetgate'],
                W_cell=None,
                b=params['GRU1back.b_resetgate']),
            updategate=L.layers.Gate(
                W_in=params['GRU1back.W_in_to_updategate'],
                W_hid=params['GRU1back.W_hid_to_updategate'],
                W_cell=None,
                b=params['GRU1back.b_updategate']),
            hidden_update=L.layers.Gate(
                W_in=params['GRU1back.W_in_to_hidden_update'],
                W_hid=params['GRU1back.W_hid_to_hidden_update'],
                W_cell=None,
                b=params['GRU1back.b_hidden_update'],
                nonlinearity=L.nonlinearities.tanh),
            hid_init=params['GRU1back.hid_init'])

        l2_pooled_forw = L2PoolingLayer(l_lv1_enc_forw)
        l2_pooled_back = L2PoolingLayer(l_lv1_enc_back)

        # concatenation of L2-pooled states
        l_lv1_enc = L.layers.ConcatLayer([l2_pooled_forw, l2_pooled_back])

        l_resh = L.layers.ReshapeLayer(l_lv1_enc, (1, 1, 2*self.lv1_rec_size))

        l_lv2_enc = L.layers.GRULayer(
            l_resh,
            num_units=self.lv2_rec_size,
            hid_init=self.context_init,
            grad_clipping=100,
            only_return_final=True,
            resetgate=L.layers.Gate(
                W_in=params['GRU2.W_in_to_resetgate'],
                W_hid=params['GRU2.W_hid_to_resetgate'],
                W_cell=None,
                b=params['GRU2.b_resetgate']),
            updategate=L.layers.Gate(
                W_in=params['GRU2.W_in_to_updategate'],
                W_hid=params['GRU2.W_hid_to_updategate'],
                W_cell=None,
                b=params['GRU2.b_updategate']),
            hidden_update=L.layers.Gate(
                W_in=params['GRU2.W_in_to_hidden_update'],
                W_hid=params['GRU2.W_hid_to_hidden_update'],
                W_cell=None,
                b=params['GRU2.b_hidden_update'],
                nonlinearity=L.nonlinearities.tanh))

        return l_lv2_enc

    def _build_decoder_net_with_params(self, params):

        l_in = L.layers.InputLayer(shape=(None, None),
                                   input_var=self.input_gen_var)

        if self.train_emb:
            l_emb = L.layers.EmbeddingLayer(l_in,
                                            input_size=self.voc_size,
                                            output_size=self.emb_size,
                                            W=params['emb.W'])
        else:
            W_init = params['emb.W'] if self.train_inds else None
            l_emb = TrainPartOfEmbsLayer(l_in,
                                         output_size=self.emb_size,
                                         input_size=self.voc_size,
                                         W=W_init,
                                         E=params['emb.E'],
                                         train_inds=self.train_inds)

        l_dec_init = L.layers.InputLayer(shape=(None, self.lv1_rec_size),
                                         input_var=self.decoder_init)

        l_dec = L.layers.GRULayer(
            l_emb,
            num_units=self.lv1_rec_size,
            grad_clipping=100,
            hid_init=l_dec_init,
            only_return_final=True,
            resetgate=L.layers.Gate(
                W_in=params['GRUdec.W_in_to_resetgate'],
                W_hid=params['GRUdec.W_hid_to_resetgate'],
                W_cell=None,
                b=params['GRUdec.b_resetgate']),
            updategate=L.layers.Gate(
                W_in=params['GRUdec.W_in_to_updategate'],
                W_hid=params['GRUdec.W_hid_to_updategate'],
                W_cell=None,
                b=params['GRUdec.b_updategate']),
            hidden_update=L.layers.Gate(
                W_in=params['GRUdec.W_in_to_hidden_update'],
                W_hid=params['GRUdec.W_hid_to_hidden_update'],
                W_cell=None,
                b=params['GRUdec.b_hidden_update'],
                nonlinearity=L.nonlinearities.tanh))

        l_Ho = L.layers.DenseLayer(l_dec,
                                   num_units=self.out_emb_size,
                                   nonlinearity=None,
                                   W=params['ho.W'],
                                   b=params['ho.b'])

        l_slice = L.layers.SliceLayer(l_emb, indices=-1, axis=1)

        l_Eo = L.layers.DenseLayer(l_slice,
                                   num_units=self.out_emb_size,
                                   W=params['eo.W'],
                                   b=None,
                                   nonlinearity=None)

        l_soft_in = L.layers.ElemwiseSumLayer([l_Ho, l_Eo])

        l_soft = L.layers.DenseLayer(l_soft_in,
                                     num_units=self.voc_size,
                                     nonlinearity=L.nonlinearities.softmax,
                                     W=params['soft.W'],
                                     b=params['soft.b'])

        return l_soft, l_dec  # l_out - probabilities, l_dec - new decoder init

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

            inp_max_len = max([len(s) for d in inp for s in d])
            inp = [
                [s + [pad] * (inp_max_len - len(s)) for s in d] for d in inp]
            inp = np.asarray(inp, dtype=np.int32)
            mask = (inp != pad).astype(np.float32)

            yield inp, mask
