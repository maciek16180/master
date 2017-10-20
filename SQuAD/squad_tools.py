import numpy as np


def load_squad_train(path, negative_path=None, NAW_token=None, NAW_char=3):
    train_words     = np.load(path + 'train_words.pkl')
    train_char      = np.load(path + 'train_char_ascii.pkl')
    train_bin_feats = np.load(path + 'train_bin_feats.pkl')

    if negative_path is None:
        print("Only positive samples.")
    else:
        print("Using negative samples from wikipedia")

        train_words_pos, train_char_pos, train_bin_feats_pos = \
            add_NAW_token([train_words, train_char, train_bin_feats], NAW_token)

        train_words_neg     = np.load(negative_path + 'train_neg_words.pkl')
        train_char_neg      = np.load(negative_path + 'train_neg_char_ascii.pkl')
        train_bin_feats_neg = np.load(negative_path + 'train_neg_bin_feats.pkl')

        train_words     = train_words_pos     + train_words_neg
        train_char      = train_char_pos      + train_char_neg
        train_bin_feats = train_bin_feats_pos + train_bin_feats_neg

    return train_words, train_char, train_bin_feats


def load_squad_dev(path, negative_path=None, NAW_token=None, NAW_char=3):
    dev           = np.load(path + 'dev.pkl')
    dev_words     = np.load(path + 'dev_words.pkl')
    dev_char      = np.load(path + 'dev_char_ascii.pkl')
    dev_bin_feats = np.load(path + 'dev_bin_feats.pkl')

    if negative_path is not None:
        dev, dev_words, dev_char, dev_bin_feats = \
            add_NAW_token([dev, dev_words, dev_char, dev_bin_feats], NAW_token)

    return dev, dev_words, dev_char, dev_bin_feats


def add_NAW_token(data, NAW_token, NAW_word=u'<not_a_word>'):
    assert type(NAW_token) == int
    assert len(data) in [3, 4] # 4 means there is a raw dev

    words, char, bin_feats = data[-3:]

    words_new     = [d[:2] + [d[2] + [NAW_token]] for d in words]
    char_new      = [[d[0], d[1] + [[1, 3, 2]]] for d in char]
    bin_feats_new = [d + [False] for d in bin_feats]

    result = words_new, char_new, bin_feats_new

    if len(data) == 4:
        raw = data[0]
        raw_new = [d[:2] + [d[2] + [NAW_word]] + [d[3]] for d in raw]
        result = (raw_new,) + result

    return result


def filter_empty_answers(train_data):
    words, char, bin_feats = train_data
    words_new     = []
    char_new      = []
    bin_feats_new = []

    for i in range(len(words)):
        if words[i][0]:
            words_new.    append(words[i])
            char_new.     append(char[i])
            bin_feats_new.append(bin_feats[i])

    return words_new, char_new, bin_feats_new


def trim_data(data, trim):
    words, char, bin_feats = data

    words_new     = []
    char_new      = []
    bin_feats_new = []

    for i in range(len(words)):
        if len(words[i][2]) > trim:
            if words[i][0][0][-1] < trim: # if trimmed paragraph contains answer
                words_new.    append(words[i][:2] + [words[i][2][:trim]])
                char_new.     append([char[i][0], char[i][1][:trim]])
                bin_feats_new.append(bin_feats[i][:trim])
        else:
            words_new.    append(words[i])
            char_new.     append(char[i])
            bin_feats_new.append(bin_feats[i])

    return words_new, char_new, bin_feats_new


def train_QANet(net, train_data, model_filename, batch_size, num_epochs=100, log_interval=200):
    for epoch in range(1, num_epochs + 1):
        print('\n\nStarting epoch {}...\n'.format(epoch))
        train_error = net.train_one_epoch(train_data=train_data,
                                          batch_size=batch_size,
                                          log_interval=log_interval)
        print('\nTraining loss:   {}'.format(train_error))
        if np.isnan(train_error):
            print("Encountered NaN, finishing...")
            break
        net.save_params(model_filename + '_ep{}'.format(epoch))

    print('Models saved as ' + model_filename)