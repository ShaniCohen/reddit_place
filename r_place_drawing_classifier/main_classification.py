# Authors: Avrahami Israeli (isabrah)
# Python version: 3.6
# Last update: 26.3.2019

# first importing dynet and setting the configuration as needed
# (random seed so we'll have a same results over and over again)
import dynet_config
dynet_config.set(random_seed=1984, mem='5000', autobatch=0)
dynet_config.set_gpu()
import dynet as dy
import warnings
import gc
import sys
if sys.platform == 'linux':
    sys.path.append('/data/home/isabrah/reddit_canvas/reddit_project_with_yalla_cluster/reddit-tools')
import os
import collections
import datetime
import pickle
from nltk.corpus import stopwords
from sklearn.feature_extraction.stop_words import ENGLISH_STOP_WORDS
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sr_classifier import utils as sr_classifier_utils
from r_place_drawing_classifier import utils as r_place_drawing_classifier_utils
from sr_classifier.reddit_data_preprocessing import RedditDataPrep
from sklearn.metrics import accuracy_score, precision_score, recall_score
from sklearn.model_selection import StratifiedKFold
import commentjson
import pandas as pd
from r_place_drawing_classifier.neural_net import mlp, single_lstm, parallel_lstm, cnn_max_pooling


warnings.simplefilter("ignore")
STOPLIST = set(stopwords.words('english') + list(ENGLISH_STOP_WORDS))

###################################################### Configurations ##################################################
config_dict = commentjson.load(open(os.path.join(os.getcwd(), 'config', 'modeling_config.json')))
machine = 'yalla' if sys.platform == 'linux' else os.environ['COMPUTERNAME']
data_path = config_dict['data_dir'][machine]
########################################################################################################################

if __name__ == "__main__":
    start_time = datetime.datetime.now()
    config_dict = r_place_drawing_classifier_utils.check_input_validity(config_dict=config_dict, machine=machine)
    sr_objects = pickle.load(open(os.path.join(data_path, 'sr_objects', config_dict['srs_obj_file'][machine]), "rb"))
    #sr_objects = sr_objects[0:20]
    # function to remove huge SRs, so parallelism can be applied
    if eval(config_dict['biggest_srs_removal']['should_remove']):
        sr_objects =\
            r_place_drawing_classifier_utils.remove_huge_srs(sr_objects=sr_objects,
                                                             quantile=config_dict['biggest_srs_removal']['quantile'])

    # adding meta features created by Alex for each SR network and data-prep to the meta-features
    missing_srs_due_to_meta_features = []
    missing_srs_due_to_authors_seq = []
    # case we want to add the network features to the explanatory features
    if eval(config_dict['meta_data_usage']['use_network']):
        net_feat_file = os.path.join(data_path, config_dict["meta_data_usage"]['network_file_path'][machine])
    else:
        net_feat_file = None
    # case we want to use the sequence of authors as text, instead of the posts themselves
    authors_seq_config = config_dict['class_model']['authors_seq']
    if eval(authors_seq_config['use_authors_seq']):
        with open(os.path.join(data_path, authors_seq_config['authors_seq_file_path'][machine]), 'rb') as f:
            conversations_seq = pickle.load(f)
    else:
        conversations_seq = None

    # looping over each sr and handling its meta features + handling the authors_seq (if needed) + sub-sampling
    for idx, cur_sr_obj in enumerate(sr_objects):
        res = cur_sr_obj.meta_features_handler(smooth_zero_features=True,
                                               net_feat_file=net_feat_file,
                                               features_to_exclude=None)
        # case there was a problem with the function, we will remove the sr from the data
        if res != 0:
            missing_srs_due_to_meta_features.append(cur_sr_obj.name)
        # case we want to model authors sequence instead of sequence of words in a submission
        if eval(config_dict['class_model']['authors_seq']['use_authors_seq']):
            try:
                cur_sr_obj.replace_sentences_with_authors_seq(conversations=conversations_seq[cur_sr_obj.name])
            # case the SR is not in the dict Alex created
            except KeyError:
                missing_srs_due_to_authors_seq.append(cur_sr_obj.name)
        # submission data under sampling
        sampling_dict = config_dict['submissions_sampling']
        if eval(sampling_dict['should_sample']):
            cur_sr_obj.subsample_submissions_data(subsample_logic=sampling_dict['sampling_logic'],
                                                  percentage=sampling_dict['percentage'],
                                                  maximum_submissions=sampling_dict['max_subm'],
                                                  seed=config_dict['random_seed'])
    duration = (datetime.datetime.now() - start_time).seconds
    combined_missing_srs = set(missing_srs_due_to_meta_features + missing_srs_due_to_authors_seq)
    print("Ended the process of adding network meta features and converting sentences into authors sequence "
          "(if it was required).\n Network features were not found for {} srs, authors sequence was not found "
          "for {} SRs. Bottom line, {} will be removed due to these 2 processes."
          "Up to now we ran for {} sec.".format(len(missing_srs_due_to_meta_features),
                                                len(missing_srs_due_to_authors_seq),
                                                len(combined_missing_srs),
                                                duration))
    # deleting the terrible object :)
    del conversations_seq
    gc.collect()
    sr_objects = [sr for sr in sr_objects if sr.name not in set(combined_missing_srs)]
    # creating the y vector feature and printing status
    y_data = []
    for idx, cur_sr_obj in enumerate(sr_objects):
        y_data += [cur_sr_obj.trying_to_draw]
        # this was used in order to create random y vector, and see results really get totally random
        # y_data += [int(np.random.choice(a=[-1, 1], size=1))]
    print("Target feature distribution is: {}".format(collections.Counter(y_data)))
    # Modeling (learning phase)
    submission_dp_obj = RedditDataPrep(is_submission_data=True, remove_stop_words=False, most_have_regex=None)
    reddit_tokenizer = submission_dp_obj.tokenize_text
    '''
    for sr in sr_objects[80:100]:
        examine_word(sr_object=sr, regex_required='rank', tokenizer=reddit_tokenizer,
                     saving_file=os.getcwd())
    print("Finished analysis phase, moving to modeling")
    '''
    # first option - the model is a BOW one (or just a simple classification one with meta features)
    if config_dict['class_model']['model_type'] == 'bow' or config_dict['class_model']['model_type'] == 'clf_meta_only':
        bow_config = config_dict['class_model']['bow_params']
        meta_features_only = True if config_dict['class_model']['model_type'] == 'clf_meta_only' else False
        cv_res, pipeline, predictions =\
            sr_classifier_utils.fit_model(sr_objects=sr_objects, y_vector=y_data, tokenizer=reddit_tokenizer,
                                          ngram_size=bow_config['ngram_size'],
                                          use_two_vectorizers=eval(bow_config['use_two_vectorizers']),
                                          clf_model=eval(config_dict['class_model']['clf_params']['clf']),
                                          folds_amount=config_dict['cv']['folds'],
                                          stop_words=STOPLIST,
                                          vectorizers_general_params=bow_config['vectorizer_params'],
                                          clf_parmas=config_dict['class_model']['clf_params'],
                                          meta_features_only=meta_features_only,
                                          return_predictions=eval(config_dict['saving_options']['raw_level_pred']),
                                          saving_models_options={'path': config_dict['results_dir'][machine],
                                                                 'model_version': config_dict['model_version']})
                                          #clf_parmas={'C': 1.0})
        if eval(config_dict['saving_options']['measures']):
            results_file = os.path.join(config_dict['results_dir'][machine], config_dict['results_file'][machine])
            r_place_drawing_classifier_utils.save_results_to_csv(results_file=results_file, start_time=start_time,
                                                                 SRs_amount=len(sr_objects), config_dict=config_dict,
                                                                 results=cv_res, saving_path=os.getcwd())

        res_summary = [(y_data[i], predictions[i, 1], sr_objects[i].name) for i in range(len(y_data))]
        print("Full modeling code has ended. Results are as follow: {}."
              "The process started at {} and finished at {}".format(cv_res, start_time, datetime.datetime.now()))

        # pulling out the most dominant features, we need to train again based on the whole data-set
        # CURRENTLY WORKS ONLY WHEN use_two_vectorizers=false!! IF WANTS TO BE FIXED - WE CAN ADD ANOTHER PARAMETER TO
        # 'vectorizer' PARAMETER
        pipeline.fit(sr_objects, y_data)
        clf = pipeline.steps[1][1]
        if config_dict['class_model']['model_type'] == 'bow':
            sr_classifier_utils.print_n_most_informative(vectorizer=[pipeline.named_steps['union'].get_params()[
                                                                         'ngram_features'].get_params()['steps'][1][1],
                                                                     pipeline.named_steps['union'].get_params()[
                                                                         'numeric_meta_features'].get_params()['steps'][1][1]],
                                                         clf=clf, N=30)
        elif config_dict['class_model']['model_type'] == 'clf_meta_only':
            sr_classifier_utils.print_n_most_informative(
                vectorizer=[pipeline.named_steps['union'].get_params()[
                                'numeric_meta_features'].get_params()['steps'][1][1]], clf=clf, N=30)

    # second option - the model is a DL one (kind of...)
    elif config_dict['class_model']['model_type'] in {'mlp', 'single_lstm', 'parallel_lstm', 'cnn_max_pooling'}:
        '''
        handling the embedding file (if we want to use an external one). This can be applied only in case we model
        the actual words in each SR, since otherwise we use the authors names, and it doesn't make sense to use
        pre defined embedding in such cases
        '''
        model = dy.ParameterCollection()
        eval_measures_dict = {'accuracy': accuracy_score, 'precision': precision_score, 'recall': recall_score}
        embedding_config = config_dict['embedding']
        if eval(embedding_config['use_pretrained']):
            embed_file = os.path.join(data_path, embedding_config['file_path'][machine])
        else:
            embed_file = None
        # training the model
        if config_dict['class_model']['model_type'] == 'mlp':
            model_obj = mlp.MLP(tokenizer=reddit_tokenizer, eval_measures=eval_measures_dict,
                                emb_size=config_dict['embedding']['emb_size'],
                                hid_size=config_dict['class_model']['nn_params']['hid_size'],
                                early_stopping=eval(config_dict['class_model']['nn_params']['early_stopping']),
                                epochs=config_dict['class_model']['nn_params']['epochs'],
                                use_meta_features=eval(config_dict['meta_data_usage']['use_meta']),
                                seed=config_dict['random_seed'],
                                use_embed=eval(config_dict['class_model']['mlp_params']['with_embed']))

        elif config_dict['class_model']['model_type'] == 'single_lstm':
            model_obj = single_lstm.SinglelLstm(tokenizer=reddit_tokenizer, eval_measures=eval_measures_dict,
                                                emb_size=config_dict['embedding']['emb_size'],
                                                hid_size=config_dict['class_model']['nn_params']['hid_size'],
                                                early_stopping=eval(config_dict['class_model']['nn_params']['early_stopping']),
                                                epochs=config_dict['class_model']['nn_params']['epochs'],
                                                use_meta_features=eval(config_dict['meta_data_usage']['use_meta']),
                                                seed=config_dict['random_seed'],
                                                use_bilstm=eval(config_dict['class_model']['parallel_lstm_params']['use_bilstm']))

        elif config_dict['class_model']['model_type'] == 'parallel_lstm':
            model_obj = parallel_lstm.ParallelLstm(tokenizer=reddit_tokenizer, eval_measures=eval_measures_dict,
                                                   emb_size=config_dict['embedding']['emb_size'],
                                                   hid_size=config_dict['class_model']['nn_params']['hid_size'],
                                                   early_stopping=eval(config_dict['class_model']['nn_params']['early_stopping']),
                                                   epochs=config_dict['class_model']['nn_params']['epochs'],
                                                   use_meta_features=eval(config_dict['meta_data_usage']['use_meta']),
                                                   seed=config_dict['random_seed'],
                                                   use_bilstm=eval(config_dict['class_model']['parallel_lstm_params']['use_bilstm']))

        elif config_dict['class_model']['model_type'] == 'cnn_max_pooling':
            model_obj = cnn_max_pooling.CnnMaxPooling(model=model, tokenizer=reddit_tokenizer, eval_measures=eval_measures_dict,
                                                      emb_size=config_dict['embedding']['emb_size'],
                                                      early_stopping=eval(config_dict['class_model']['nn_params']['early_stopping']),
                                                      epochs=config_dict['class_model']['nn_params']['epochs'],
                                                      use_meta_features=eval(config_dict['meta_data_usage']['use_meta']),
                                                      seed=config_dict['random_seed'],
                                                      batch_size=config_dict['class_model']['nn_params']['batch_size'],
                                                      filter_size=config_dict['class_model']['cnn_max_pooling_parmas']['filter_size'],
                                                      win_size=config_dict['class_model']['cnn_max_pooling_parmas']['win_size'])

        cv_obj = StratifiedKFold(n_splits=config_dict['cv']['folds'], random_state=config_dict['random_seed'])
        cv_obj.get_n_splits(sr_objects, y_data)
        all_test_data_pred = []
        for cv_idx, (train_index, test_index) in enumerate(cv_obj.split(sr_objects, y_data)):
            print("Fold {} starts".format(cv_idx))
            cur_train_sr_objects = [sr_objects[i] for i in train_index]
            cur_test_sr_objects = [sr_objects[i] for i in test_index]
            cur_y_train = [y_data[i] for i in train_index]
            cur_y_test = [y_data[i] for i in test_index]
            cur_results, cur_model, cur_test_predictions = model_obj.fit_predict(train_data=cur_train_sr_objects,
                                                                                 test_data=cur_test_sr_objects,
                                                                                 embedding_file=embed_file)

            print("Fold # {} has ended, updated results list is: {}".format(cv_idx, cur_results))
            # save the current model to file if required
            if eval(config_dict['saving_options']['models']):
                # since we need to save the model for each fold, we will give each one a different name
                #cur_model_ver = config_dict['model_version'] + 'fold' + str(cv_idx)
                model_obj.save_model(path=config_dict['results_dir'][machine],
                                     model_version=config_dict['model_version'], fold=cv_idx)
            cur_test_sr_names = [sr_obj.name for sr_obj in cur_test_sr_objects]
            all_test_data_pred.extend([(y, pred, name) for name, y, pred in
                                       zip(cur_test_sr_names, cur_y_test, cur_test_predictions)])

        eval_results = model_obj.eval_results
        # saving results to file if needed
        if eval(config_dict['saving_options']['measures']):
            dl_params_tp_save = model_obj.__dict__
            dl_params_tp_save.pop('w2i')
            dl_params_tp_save.pop('t2i')
            dl_params_tp_save.pop('eval_results')
            dl_params_tp_save.pop('eval_measures')
            results_file = os.path.join(config_dict['results_dir'][machine], config_dict['results_file'][machine])
            r_place_drawing_classifier_utils.save_results_to_csv(results_file=results_file, start_time=start_time,
                                                                 SRs_amount=len(sr_objects), config_dict=config_dict,
                                                                 results=eval_results, saving_path=os.getcwd())
        print("Full modeling code has ended. Results are as follow: {}. \nThe process started at {}"
              " and finished at {}".format(eval_results, start_time, datetime.datetime.now()))

        res_summary = all_test_data_pred

    # anyway, at the end of the code we will save results if it is required
    if eval(config_dict['saving_options']['raw_level_pred']):
        cur_folder_name = os.path.join(config_dict['results_dir'][machine], "model_" + config_dict['model_version'])
        if not os.path.exists(cur_folder_name):
            os.makedirs(cur_folder_name)
        res_summary_df = pd.DataFrame(res_summary, columns=['true_y', 'prediction_to_draw', 'sr_name'])
        res_summary_df.to_csv(os.path.join(cur_folder_name,
                                           ''.join(['results_summary_model', config_dict['model_version'], '.csv'])),
                              index=False)
    if eval(config_dict['saving_options']['configuration']):
        cur_folder_name = os.path.join(config_dict['results_dir'][machine], "model_" + config_dict['model_version'])
        if not os.path.exists(cur_folder_name):
            os.makedirs(cur_folder_name)
        file_path = os.path.join(cur_folder_name, 'config_model_' + config_dict['model_version'] + '.json')
        with open(file_path, 'w') as fp:
            commentjson.dump(config_dict, fp, indent=2)

