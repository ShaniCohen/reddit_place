# Authors: Avrahami Israeli (isabrah)
# Python version: 3.7
# Last update: 29.11.2018

from r_place_drawing_classifier.neural_net.nn_classifier import NNClassifier
import warnings
import random
#import dynet_config
# set random seed to have the same result each time
#dynet_config.set(random_seed=1984, mem='5000', autobatch=0)
#dynet_config.set_gpu()
import dynet as dy
import numpy as np
import time


class MLP(NNClassifier):
    """
    multi layer perceptron implementation, using dynet package
    this class inherits from NNClassifier, and is a special case of a NN model. Model implemented here is the most
    simple one, using only meta feature (+embedded words, optional)

    :param use_embed: boolean
        whether ot not to apply an embedding phase in order to use words along modeling
    """
    def __init__(self, tokenizer, eval_measures, emb_size=100, hid_size=100, early_stopping=True,
                 epochs=10, use_meta_features=True, seed=1984, use_embed=False):
        super(MLP, self).__init__(tokenizer=tokenizer, eval_measures=eval_measures, emb_size=emb_size,
                                  hid_size=hid_size, early_stopping=early_stopping, epochs=epochs,
                                  use_meta_features=use_meta_features, seed=seed)
        self.use_embed = use_embed

    def fit_predict(self, train_data, test_data, embedding_file=None):
        """
        fits a model and predicts probability over the test set
        :param train_data: list
            list of sr objects to be used for training
        :param test_data: list
            list of sr objects to be as the test data set
        :param embedding_file: str
            the external embedding matrix to be used along modeling (only the explicit file location is provided here).
            If None - external embedding is not used
        :return: tuple
            tuple with 3 variables:
            self.eval_results, model, test_predicitons
            1. eval_results: dictionary with evaluation measures over the test set
            2. model: the MLP trained model which was used
            3. test_predicitons: list with predictions to each sr in the test dataset
        """
        if embedding_file is not None and not self.use_embed:
            warnings.warn("Note that you provedid an external embedding file, while you configured settings not to use"
                          "embedding phase along model building. The embedding file will not be used")
        if not self.use_embed:
            return self.fit_simple_mlp(train_data=train_data, test_data=test_data)
        else:
            return self.fit_embedded_mlp(train_data=train_data, test_data=test_data, embedding_file=embedding_file)

    def fit_simple_mlp(self, train_data, test_data):
        """
        fits an MLP model over the train data and evaluates results over the test data
        :param train_data: list
            list of sr objects to be used for training
        :param test_data: list
        :return: tuple
            tuple with 3 variables:
            self.eval_results, model, test_predicitons
            1. eval_results: dictionary with evaluation measures over the test set
            2. model: the MLP trained model which was used
            3. test_predicitons: list with predictions to each sr in the test dataset
        """
        random.seed(self.seed)
        random.shuffle(train_data)
        # data prep to meta features
        self.data_prep_meta_features(train_data=train_data, test_data=test_data, update_objects=True)

        # pulling out the meta features and the tag (of train and test)
        train_meta_data = [sr_obj.explanatory_features for sr_obj in train_data]
        test_meta_data = [sr_obj.explanatory_features for sr_obj in test_data]
        y_train = [sr_obj.trying_to_draw for sr_obj in train_data]
        y_test = [sr_obj.trying_to_draw for sr_obj in test_data]
        meta_data_dim = len(list(train_meta_data[0].keys()))
        # Start DyNet and define trainer
        model = dy.Model()
        trainer = dy.SimpleSGDTrainer(model)
        dy.renew_cg()

        # dynet model's params
        W = model.add_parameters((self.hid_size, meta_data_dim))
        b = model.add_parameters(self.hid_size)
        V = model.add_parameters((1, self.hid_size))
        a = model.add_parameters(1)
        x = dy.vecInput(meta_data_dim)
        h = dy.tanh((W * x) + b)
        y = dy.scalarInput(0)
        y_pred = dy.logistic((V * h) + a)
        loss = dy.binary_log_loss(y_pred, y)
        mloss = [0.0, 0.0]  # we always save the current run loss and the prev one (for early stopping purposes

        # iterations over the epochs
        for ITER in range(self.epochs):
            # checking the early stopping criterion
            if self.early_stopping and (ITER >= (self.epochs * 1.0 / 2)) \
                    and ((mloss[0]-mloss[1]) * 1.0 / mloss[0]) <= 0.01:
                print("Early stopping has been applied since improvement was not greater than 1%")
                break
            # Perform training
            start = time.time()
            cur_mloss=0.0
            for idx, (cur_sr_dict, tag) in enumerate(zip(train_meta_data, y_train)):
                # create graph for computing loss
                cur_sr_values_ordered = [value for key, value in sorted(cur_sr_dict.items())]
                x.set(cur_sr_values_ordered)
                tag_normalized = 1 if tag == 1 else 0
                y.set(tag_normalized)
                # loss calc
                cur_mloss += loss.value()
                loss.backward()
                trainer.update()
            # updating the mloss for early stopping purposes
            mloss[0] = mloss[1]
            mloss[1] = cur_mloss
            print("iter %r: train loss/sr=%.4f, time=%.2fs" % (ITER, cur_mloss / len(y_train), time.time() - start))
            # Perform testing validation
            test_correct = 0.0
            y_pred = dy.logistic((V * h) + a)
            for idx, (cur_sr_dict, tag) in enumerate(zip(test_meta_data, y_test)):
                cur_sr_values_ordered = [value for key, value in sorted(cur_sr_dict.items())]
                x.set(cur_sr_values_ordered)
                y_pred_value = y_pred.value()
                if (y_pred_value >= .5 and tag == 1) or (y_pred_value <= .5 and tag == -1):
                    test_correct += 1
            print("iter %r: test acc=%.4f" % (ITER, test_correct / len(y_test)))
        # Perform testing validation after all batches ended
        test_predicitons = []
        test_correct = 0.0
        for idx, (cur_sr_dict, tag) in enumerate(zip(test_meta_data, y_test)):
            cur_sr_values_ordered = [value for key, value in sorted(cur_sr_dict.items())]
            x.set(cur_sr_values_ordered)
            y_pred_value = y_pred.value()
            test_predicitons.append(y_pred_value)
            if (y_pred_value >= .5 and tag == 1) or (y_pred_value <= .5 and tag == -1):
                test_correct += 1
        self.calc_eval_measures(y_true=y_test, y_pred=test_predicitons, nomalize_y=True)
        print("final test acc=%.4f" % (test_correct / len(y_test)))
        return self.eval_results, model, test_predicitons

    def _calc_scores_embedded_mlp(self, sentences, W_emb, W_mlp, b_mlp, V_mlp, a_mlp, meta_data=None):
        """
        calculating the score for a a NN network (in a specific state along learning phase)
        :param sentences: list
            list of lists of sentences (represented already as numbers and not letters)
        :param W_emb: lookup parameter (dynet obj). size: (emb_size x nwords)
            matrix holding the word embedding values
        :param W_mlp: model parameter (dynet obj). size: (hid_size, emb_size + meta_data_dim)
            matrix holding weights of the mlp phase
        :param b_mlp: model parameter (dynet obj). size: (hid_size,)
            vector holding weights of intercept for each hidden state
        :param V_mlp: model parameter (dynet obj). size: (2, hid_size)
            matrix holding weights of the logisitc regression phase. 2 is there due to the fact we are in a binary
            classification
        :param a_mlp: model parameter (dynet obj). size: (1,)
            intercept value for the logistic regression phase
        :param meta_data: dict or None
            meta data features for the model. If None - meta data is not used
        :return: dynet parameter. size: (2,)
            prediction of the instance to be a drawing one according to the model (vector of 2, first place is the
            probability to be a drawing team)
        """
        dy.renew_cg()
        # sentences_len = len(sentences)
        word_embs = [[dy.lookup(W_emb, w) for w in words] for words in sentences]
        # taking the average over all words
        first_layer_avg = dy.average([dy.average(w_em) for w_em in word_embs])
        # case we don't wish to use meta features for the model
        if meta_data is None:
            h = dy.tanh((W_mlp * first_layer_avg) + b_mlp)
            prediction = dy.logistic((V_mlp * h) + a_mlp)
        else:
            meta_data_ordered = [value for key, value in sorted(meta_data.items())]
            meta_data_vector = dy.inputVector(meta_data_ordered)
            first_layer_avg_and_meta_data = dy.concatenate([first_layer_avg, meta_data_vector])
            h = dy.tanh((W_mlp * first_layer_avg_and_meta_data) + b_mlp)
            prediction = dy.logistic((V_mlp * h) + a_mlp)
        return prediction

    def fit_embedded_mlp(self, train_data, test_data, embedding_file=None):
        """
        fits an MLP model with embedding layer
        :param train_data: list
            list of sr objects to be used as train set
        :param test_data: list
            list of sr objects to be used as train set
        :param embedding_file: str
            the path to the exact embedding file to be used. This should be a txt file, each row represents
            a word and it's embedding (separated by whitespace). Example can be taken from 'glove' pre-trained models
            If None, we build an embedding from random normal distribution
        :return: tuple
            tuple with 3 variables:
            self.eval_results, model, test_predicitons
            1. eval_results: dictionary with evaluation measures over the test set
            2. model: the MLP trained model which was used
            3. test_predicitons: list with predictions to each sr in the test dataset
        """
        # case we wish to use meta features along modeling, we need to prepare the SRs objects for this
        if self.use_meta_features:
            train_meta_data, test_meta_data = \
                self.data_prep_meta_features(train_data=train_data, test_data=test_data, update_objects=False)
            meta_data_dim = len(train_meta_data[list(train_meta_data.keys())[0]])
        else:
            train_meta_data = None
            test_meta_data = None
            meta_data_dim = 0
        # next we are creating the input for the algorithm. train_data_for_dynet will contain list of lists. Each inner
        # list contains the words index relevant to the specific sentence
        train_data_for_dynet = list(self.get_reddit_sentences(sr_objects=train_data))
        train_data_names = [i[2] for i in train_data_for_dynet]  # list of train sr names
        train_data_for_dynet = [(i[0], i[1]) for i in train_data_for_dynet]

        # test_data_for_dynet will contain list of lists. Each inner list contains the words index relevant to the
        # specific sentence
        test_data_for_dynet = list(self.get_reddit_sentences(sr_objects=test_data))
        test_data_names = [i[2] for i in test_data_for_dynet]   # list of test sr names
        # Need to check here that the order is saved!!!!
        test_data_for_dynet = [(i[0], i[1]) for i in test_data_for_dynet]

        # Start DyNet and define trainer
        model = dy.Model()
        trainer = dy.AdamTrainer(model)
        # Define the model
        # Word embeddings part
        if embedding_file is None:
            W_emb = model.add_lookup_parameters((self.nwords, self.emb_size))
        else:
            external_embedding = self.build_embedding_matrix(embedding_file)
            W_emb = model.add_lookup_parameters((self.nwords, self.emb_size), init=external_embedding)
        # Last layer with network is an MLP one, case we are not using meta data, meta_data_dim will be zero
        # and hence not relevant
        W_mlp = model.add_parameters((self.hid_size, self.emb_size + meta_data_dim))
        b_mlp = model.add_parameters(self.hid_size)
        V_mlp = model.add_parameters((self.ntags, self.hid_size))
        a_mlp = model.add_parameters(1)

        mloss = [0.0, 0.0]  # we always save the current run loss and the prev one (for early stopping purposes
        for ITER in range(self.epochs):
            # checking the early stopping criterion
            if self.early_stopping and (ITER >= (self.epochs * 1.0 / 2)) \
                    and ((mloss[0]-mloss[1]) * 1.0 / mloss[0]) <= 0.01:
                print("Early stopping has been applied since improvement was not greater than 1%")
                break
            # Perform training
            #random.seed(self.seed)
            #random.shuffle(train_data_for_dynet)
            start = time.time()
            cur_mloss = 0.0
            # looping over each sentence in the train data, calculating the loss and updating the model
            for idx, (sentences, tag) in enumerate(train_data_for_dynet):
                cur_meta_data = train_meta_data[train_data_names[idx]] if self.use_meta_features else None
                my_loss =\
                    dy.pickneglogsoftmax(self._calc_scores_embedded_mlp(sentences=sentences, W_emb=W_emb,
                                                                        W_mlp=W_mlp, b_mlp=b_mlp, V_mlp=V_mlp,
                                                                        a_mlp=a_mlp, meta_data=cur_meta_data), tag)
                cur_mloss += my_loss.value()
                my_loss.backward()
                trainer.update()
            # updating the mloss for early stopping purposes
            mloss[0] = mloss[1]
            mloss[1] = cur_mloss
            print("iter %r: train loss/sr=%.4f, time=%.2fs" % (ITER, cur_mloss / len(train_data_for_dynet),
                                                               time.time() - start))
            # Perform testing validation
            test_correct = 0.0
            for idx, (words, tag) in enumerate(test_data_for_dynet):
                cur_meta_data = test_meta_data[test_data_names[idx]] if self.use_meta_features else None
                scores = self._calc_scores_embedded_mlp(sentences=words, W_emb=W_emb, W_mlp=W_mlp, b_mlp=b_mlp,
                                                        V_mlp=V_mlp, a_mlp=a_mlp, meta_data=cur_meta_data).npvalue()
                predict = np.argmax(scores)
                if predict == tag:
                    test_correct += 1
            print("iter %r: test acc=%.4f" % (ITER, test_correct / len(test_data_for_dynet)))
        # Perform testing validation after all batches ended
        test_correct = 0.0
        test_predictions = []
        for idx, (words, tag) in enumerate(test_data_for_dynet):
            cur_meta_data = test_meta_data[test_data_names[idx]] if self.use_meta_features else None
            cur_score = self._calc_scores_embedded_mlp(sentences=words, W_emb=W_emb, W_mlp=W_mlp, b_mlp=b_mlp,
                                                       V_mlp=V_mlp, a_mlp=a_mlp, meta_data=cur_meta_data).npvalue()
            # adding the prediction of the sr to draw (to be label 1) and calculating the acc on the fly
            test_predictions.append(cur_score[1])
            predict = np.argmax(cur_score)
            if predict == tag:
                test_correct += 1

        y_test = [a[1] for a in test_data_for_dynet]
        self.calc_eval_measures(y_true=y_test, y_pred=test_predictions, nomalize_y=True)
        print("final test acc=%.4f" % (test_correct / len(y_test)))

        return self.eval_results, model, test_predictions
