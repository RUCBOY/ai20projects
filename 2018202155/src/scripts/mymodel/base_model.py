from os.path import join
import abc
import time
import numpy as np
import tensorflow as tf
from tensorflow import keras
from tqdm import tqdm

__all__ = ["BaseModel"]


def cal_metric(labels, preds, metrics):
    """Calculate metrics,such as auc, logloss.
    
    FIXME: 
        refactor this with the reco metrics and make it explicit.
    """
    res = {}
    for metric in metrics:
        if metric == "auc":
            auc = roc_auc_score(np.asarray(labels), np.asarray(preds))
            res["auc"] = round(auc, 4)
        elif metric == "rmse":
            rmse = mean_squared_error(np.asarray(labels), np.asarray(preds))
            res["rmse"] = np.sqrt(round(rmse, 4))
        elif metric == "logloss":
            # avoid logloss nan
            preds = [max(min(p, 1.0 - 10e-12), 10e-12) for p in preds]
            logloss = log_loss(np.asarray(labels), np.asarray(preds))
            res["logloss"] = round(logloss, 4)
        elif metric == "acc":
            pred = np.asarray(preds)
            pred[pred >= 0.5] = 1
            pred[pred < 0.5] = 0
            acc = accuracy_score(np.asarray(labels), pred)
            res["acc"] = round(acc, 4)
        elif metric == "f1":
            pred = np.asarray(preds)
            pred[pred >= 0.5] = 1
            pred[pred < 0.5] = 0
            f1 = f1_score(np.asarray(labels), pred)
            res["f1"] = round(f1, 4)
        elif metric == "mean_mrr":
            mean_mrr = np.mean(
                [
                    mrr_score(each_labels, each_preds)
                    for each_labels, each_preds in zip(labels, preds)
                ]
            )
            res["mean_mrr"] = round(mean_mrr, 4)
        elif metric.startswith("ndcg"):  # format like:  ndcg@2;4;6;8
            ndcg_list = [1, 2]
            ks = metric.split("@")
            if len(ks) > 1:
                ndcg_list = [int(token) for token in ks[1].split(";")]
            for k in ndcg_list:
                ndcg_temp = np.mean(
                    [
                        ndcg_score(each_labels, each_preds, k)
                        for each_labels, each_preds in zip(labels, preds)
                    ]
                )
                res["ndcg@{0}".format(k)] = round(ndcg_temp, 4)
        elif metric.startswith("hit"):  # format like:  hit@2;4;6;8
            hit_list = [1, 2]
            ks = metric.split("@")
            if len(ks) > 1:
                hit_list = [int(token) for token in ks[1].split(";")]
            for k in hit_list:
                hit_temp = np.mean(
                    [
                        hit_score(each_labels, each_preds, k)
                        for each_labels, each_preds in zip(labels, preds)
                    ]
                )
                res["hit@{0}".format(k)] = round(hit_temp, 4)
        elif metric == "group_auc":
            group_auc = np.mean(
                [
                    roc_auc_score(each_labels, each_preds)
                    for each_labels, each_preds in zip(labels, preds)
                ]
            )
            res["group_auc"] = round(group_auc, 4)
        else:
            raise ValueError("not define this metric {0}".format(metric))
    return res




class BaseModel:
    """Basic class of models

    Attributes:
        hparams (obj): A tf.contrib.training.HParams object, hold the entire set of hyperparameters.
        iterator_creator_train (obj): An iterator to load the data in trainning steps.
        iterator_creator_train (obj): An iterator to load the data in testing steps.
        graph (obj): An optional graph.
        seed (int): Random seed.
    """

    def __init__(
        self, hparams, iterator_creator, seed=None,
    ):
        """Initializing the model. Create common logics which are needed by all deeprec models, such as loss function, 
        parameter set.

        Args:
            hparams (obj): A tf.contrib.training.HParams object, hold the entire set of hyperparameters.
            iterator_creator_train (obj): An iterator to load the data in trainning steps.
            iterator_creator_train (obj): An iterator to load the data in testing steps.
            graph (obj): An optional graph.
            seed (int): Random seed.
        """
        self.seed = seed
        tf.compat.v1.set_random_seed(seed)
        np.random.seed(seed)

        self.train_iterator = iterator_creator(
            hparams, hparams.npratio, col_spliter="\t",
        )
        self.test_iterator = iterator_creator(hparams, col_spliter="\t",)

        self.hparams = hparams
        self.support_quick_scoring = hparams.support_quick_scoring

        self.model, self.scorer = self._build_graph()

        self.loss = self._get_loss()
        self.train_optimizer = self._get_opt()

        self.model.compile(loss=self.loss, optimizer=self.train_optimizer)

        # set GPU use with demand growth
        gpu_options = tf.compat.v1.GPUOptions(allow_growth=True)

    def _init_embedding(self, file_path):
        """Load pre-trained embeddings as a constant tensor.
        
        Args:
            file_path (str): the pre-trained glove embeddings file path.

        Returns:
            np.array: A constant numpy array.
        """

        return np.load(file_path)


    def _get_loss(self):
        """Make loss function, consists of data loss and regularization loss
        
        Returns:
            obj: Loss function or loss function name
        """
        if self.hparams.loss == "cross_entropy_loss":
            data_loss = "categorical_crossentropy"
        elif self.hparams.loss == "log_loss":
            data_loss = "binary_crossentropy"
        else:
            raise ValueError("this loss not defined {0}".format(self.hparams.loss))
        return data_loss

    def _get_opt(self):
        """Get the optimizer according to configuration. Usually we will use Adam.
        Returns:
            obj: An optimizer.
        """
        lr = self.hparams.learning_rate
        optimizer = self.hparams.optimizer

        if optimizer == "adam":
            train_opt = keras.optimizers.Adam(lr=lr)

        return train_opt

    def _get_pred(self, logit, task):
        """Make final output as prediction score, according to different tasks.
        
        Args:
            logit (obj): Base prediction value.
            task (str): A task (values: regression/classification)
        
        Returns:
            obj: Transformed score
        """
        if task == "regression":
            pred = tf.identity(logit)
        elif task == "classification":
            pred = tf.sigmoid(logit)
        else:
            raise ValueError(
                "method must be regression or classification, but now is {0}".format(
                    task
                )
            )
        return pred

    def train(self, train_batch_data):
        """Go through the optimization step once with training data in feed_dict.

        Args:
            sess (obj): The model session object.
            feed_dict (dict): Feed values to train the model. This is a dictionary that maps graph elements to values.

        Returns:
            list: A list of values, including update operation, total loss, data loss, and merged summary.
        """
        train_input, train_label = self._get_input_label_from_iter(train_batch_data)
        rslt = self.model.train_on_batch(train_input, train_label)
        return rslt

    def eval(self, eval_batch_data):
        """Evaluate the data in feed_dict with current model.

        Args:
            sess (obj): The model session object.
            feed_dict (dict): Feed values for evaluation. This is a dictionary that maps graph elements to values.

        Returns:
            list: A list of evaluated results, including total loss value, data loss value,
                predicted scores, and ground-truth labels.
        """
        eval_input, eval_label = self._get_input_label_from_iter(eval_batch_data)
        imp_index = eval_batch_data["impression_index_batch"]

        pred_rslt = self.scorer.predict_on_batch(eval_input)

        return pred_rslt, eval_label, imp_index

    def fit(
        self,
        train_news_file,
        train_behaviors_file,
        valid_news_file,
        valid_behaviors_file,
        test_news_file=None,
        test_behaviors_file=None,
    ):
        """Fit the model with train_file. Evaluate the model on valid_file per epoch to observe the training status.
        If test_news_file is not None, evaluate it too.
        
        Args:
            train_file (str): training data set.
            valid_file (str): validation set.
            test_news_file (str): test set.

        Returns:
            obj: An instance of self.
        """

        for epoch in range(1, self.hparams.epochs + 1):
            step = 0
            self.hparams.current_epoch = epoch
            epoch_loss = 0
            train_start = time.time()

            tqdm_util = tqdm(
                self.train_iterator.load_data_from_file(
                    train_news_file, train_behaviors_file
                )
            )

            for batch_data_input in tqdm_util:

                step_result = self.train(batch_data_input)
                step_data_loss = step_result

                epoch_loss += step_data_loss
                step += 1
                if step % self.hparams.show_step == 0:
                    tqdm_util.set_description(
                        "step {0:d} , total_loss: {1:.4f}, data_loss: {2:.4f}".format(
                            step, epoch_loss / step, step_data_loss
                        )
                    )

            train_end = time.time()
            train_time = train_end - train_start

            eval_start = time.time()

            train_info = ",".join(
                [
                    str(item[0]) + ":" + str(item[1])
                    for item in [("logloss loss", epoch_loss / step)]
                ]
            )

            eval_res = self.run_eval(valid_news_file, valid_behaviors_file)
            eval_info = ", ".join(
                [
                    str(item[0]) + ":" + str(item[1])
                    for item in sorted(eval_res.items(), key=lambda x: x[0])
                ]
            )
            if test_news_file is not None:
                test_res = self.run_eval(test_news_file, test_behaviors_file)
                test_info = ", ".join(
                    [
                        str(item[0]) + ":" + str(item[1])
                        for item in sorted(test_res.items(), key=lambda x: x[0])
                    ]
                )
            eval_end = time.time()
            eval_time = eval_end - eval_start

            if test_news_file is not None:
                print(
                    "at epoch {0:d}".format(epoch)
                    + "\ntrain info: "
                    + train_info
                    + "\neval info: "
                    + eval_info
                    + "\ntest info: "
                    + test_info
                )
            else:
                print(
                    "at epoch {0:d}".format(epoch)
                    + "\ntrain info: "
                    + train_info
                    + "\neval info: "
                    + eval_info
                )
            print(
                "at epoch {0:d} , train time: {1:.1f} eval time: {2:.1f}".format(
                    epoch, train_time, eval_time
                )
            )

        return self

    def group_labels(self, labels, preds, group_keys):
        """Devide labels and preds into several group according to values in group keys.

        Args:
            labels (list): ground truth label list.
            preds (list): prediction score list.
            group_keys (list): group key list.

        Returns:
            all_labels: labels after group.
            all_preds: preds after group.

        """

        all_keys = list(set(group_keys))
        all_keys.sort()
        group_labels = {k: [] for k in all_keys}
        group_preds = {k: [] for k in all_keys}

        for l, p, k in zip(labels, preds, group_keys):
            group_labels[k].append(l)
            group_preds[k].append(p)

        all_labels = []
        all_preds = []
        for k in all_keys:
            all_labels.append(group_labels[k])
            all_preds.append(group_preds[k])

        return all_keys, all_labels, all_preds

    def run_eval(self, news_filename, behaviors_file):
        """Evaluate the given file and returns some evaluation metrics.
        
        Args:
            filename (str): A file name that will be evaluated.

        Returns:
            dict: A dictionary contains evaluation metrics.
        """

        if self.support_quick_scoring:
            _, group_labels, group_preds = self.run_fast_eval(
                news_filename, behaviors_file
            )
        else:
            _, group_labels, group_preds = self.run_slow_eval(
                news_filename, behaviors_file
            )
        res = cal_metric(group_labels, group_preds, self.hparams.metrics)
        return res

    def user(self, batch_user_input):
        user_input = self._get_user_feature_from_iter(batch_user_input)
        user_vec = self.userencoder.predict_on_batch(user_input)
        user_index = batch_user_input["impr_index_batch"]

        return user_index, user_vec

    def news(self, batch_news_input):
        news_input = self._get_news_feature_from_iter(batch_news_input)
        news_vec = self.newsencoder.predict_on_batch(news_input)
        news_index = batch_news_input["news_index_batch"]

        return news_index, news_vec

    def run_user(self, news_filename, behaviors_file):
        if not hasattr(self, "userencoder"):
            raise ValueError("model must have attribute userencoder")

        user_indexes = []
        user_vecs = []
        for batch_data_input in tqdm(
            self.test_iterator.load_user_from_file(news_filename, behaviors_file)
        ):
            user_index, user_vec = self.user(batch_data_input)
            user_indexes.extend(np.reshape(user_index, -1))
            user_vecs.extend(user_vec)

        return dict(zip(user_indexes, user_vecs))

    def run_news(self, news_filename):
        if not hasattr(self, "newsencoder"):
            raise ValueError("model must have attribute newsencoder")

        news_indexes = []
        news_vecs = []
        for batch_data_input in tqdm(
            self.test_iterator.load_news_from_file(news_filename)
        ):
            news_index, news_vec = self.news(batch_data_input)
            news_indexes.extend(np.reshape(news_index, -1))
            news_vecs.extend(news_vec)

        return dict(zip(news_indexes, news_vecs))

    def run_slow_eval(self, news_filename, behaviors_file):
        preds = []
        labels = []
        imp_indexes = []

        for batch_data_input in tqdm(
            self.test_iterator.load_data_from_file(news_filename, behaviors_file)
        ):
            step_pred, step_labels, step_imp_index = self.eval(batch_data_input)
            preds.extend(np.reshape(step_pred, -1))
            labels.extend(np.reshape(step_labels, -1))
            imp_indexes.extend(np.reshape(step_imp_index, -1))

        group_impr_indexes, group_labels, group_preds = self.group_labels(
            labels, preds, imp_indexes
        )
        return group_impr_indexes, group_labels, group_preds

    def run_fast_eval(self, news_filename, behaviors_file):
        news_vecs = self.run_news(news_filename)
        user_vecs = self.run_user(news_filename, behaviors_file)

        self.news_vecs = news_vecs
        self.user_vecs = user_vecs

        group_impr_indexes = []
        group_labels = []
        group_preds = []

        for (impr_index, news_index, user_index, label,) in tqdm(
            self.test_iterator.load_impression_from_file(behaviors_file)
        ):
            pred = np.dot(
                np.stack([news_vecs[i] for i in news_index], axis=0),
                user_vecs[impr_index],
            )
            group_impr_indexes.append(impr_index)
            group_labels.append(label)
            group_preds.append(pred)

        return group_impr_indexes, group_labels, group_preds
