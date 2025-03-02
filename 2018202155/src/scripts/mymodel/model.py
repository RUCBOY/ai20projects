import numpy as np
import tensorflow as tf
import tensorflow.keras as keras
from tensorflow.keras import layers


from .base_model import BaseModel
from .layers import AttLayer2, SelfAttention,ComputeMasking,OverwriteMasking

__all__ = ["Model"]


class Model(BaseModel):
    """
    Attributes:
        word2vec_embedding (numpy.array): Pretrained word embedding matrix.
        hparam (obj): Global hyper-parameters.
    """

    def __init__(
        self, hparams, iterator_creator, seed=None,
    ):
        """Initialization steps
        Compared with the BaseModel, Model need word embedding.
        After creating word embedding matrix, BaseModel's __init__ method will be called.
        
        Args:
            hparams (obj): Global hyper-parameters. Some key setttings such as head_num and head_dim are there.
            iterator_creator_train(obj): data loader class for train data.
            iterator_creator_test(obj): data loader class for test and validation data
        """
        self.word2vec_embedding = self._init_embedding(hparams.wordEmb_file)
        self.hparam = hparams

        super().__init__(
            hparams, iterator_creator, seed=seed,
        )

    def _get_input_label_from_iter(self, batch_data):
        """ get input and labels for trainning from iterator

        Args: 
            batch data: input batch data from iterator

        Returns:
            list: input feature fed into model (clicked_title_batch & candidate_title_batch)
            array: labels
        """
        input_feat = [
            batch_data["clicked_title_batch"],
            batch_data["clicked_ab_batch"],
            batch_data["clicked_vert_batch"],
            batch_data["clicked_subvert_batch"],
            batch_data["candidate_title_batch"],
            batch_data["candidate_ab_batch"],
            batch_data["candidate_vert_batch"],
            batch_data["candidate_subvert_batch"],
            batch_data["user_index_batch"],
            batch_data["candidate_title_batch"],
        ]
        input_label = batch_data["labels"]
        return input_feat, input_label

    def _get_user_feature_from_iter(self, batch_data):
        """ get input of user encoder 
        Args:
            batch_data: input batch data from user iterator
        
        Returns:
            array: input user feature (clicked title batch)
        """
        input_feature = [
            batch_data["clicked_title_batch"],
            batch_data["clicked_ab_batch"],
            batch_data["clicked_vert_batch"],
            batch_data["clicked_subvert_batch"],
            batch_data["user_index_batch"],
        ]
        input_feature = np.concatenate(input_feature, axis=-1)
        return input_feature

    def _get_news_feature_from_iter(self, batch_data):
        """ get input of news encoder
        Args:
            batch_data: input batch data from news iterator
        
        Returns:
            array: input news feature (candidate title batch)
        """
        #nrms lstur
        return batch_data["candidate_title_batch"]

        #naml
        input_feature = [
            batch_data["candidate_title_batch"],
            batch_data["candidate_ab_batch"],
            batch_data["candidate_vert_batch"],
            batch_data["candidate_subvert_batch"],
        ]
        input_feature = np.concatenate(input_feature, axis=-1)
        return input_feature

    def _build_graph(self):
        """Build model and scorer.

        Returns:
            obj: a model used to train.
            obj: a model used to evaluate and inference.
        """
        hparams = self.hparams
        model, scorer = self._build_model()
        return model, scorer

    def _build_userencoder(self, newsencoder):
        """The main function to create user encoder of Model.

        Args:
            titleencoder(obj): the news encoder of Model. 

        Return:
            obj: the user encoder of Model.
        """
        hparams = self.hparams
        his_input_title_body_verts = keras.Input(
            shape=(hparams.his_size, hparams.title_size + hparams.body_size + 2), dtype="int32"
        )

        click_news_presents = layers.TimeDistributed(newsencoder)(
            his_input_title_body_verts
        )

        user_indexes = keras.Input(shape=(1,), dtype="int32")

        user_embedding_layer = layers.Embedding(
            len(self.train_iterator.uid2index),
            hparams.gru_unit,
            trainable=True,
            embeddings_initializer="zeros",
        )

        long_u_emb = layers.Reshape((hparams.gru_unit,))(
            user_embedding_layer(user_indexes)
        )

        if type == "ini":
            user_present = layers.GRU(
                hparams.gru_unit,
                kernel_initializer=keras.initializers.glorot_uniform(seed=self.seed),
                recurrent_initializer=keras.initializers.glorot_uniform(seed=self.seed),
                bias_initializer=keras.initializers.Zeros(),
            )(
                layers.Masking(mask_value=0.0)(click_news_presents),
                initial_state=[long_u_emb],
            )
        elif type == "con":
            short_uemb = layers.GRU(
                hparams.gru_unit,
                kernel_initializer=keras.initializers.glorot_uniform(seed=self.seed),
                recurrent_initializer=keras.initializers.glorot_uniform(seed=self.seed),
                bias_initializer=keras.initializers.Zeros(),
            )(layers.Masking(mask_value=0.0)(click_news_presents))

            user_present = layers.Concatenate()([short_uemb, long_u_emb])
            user_present = layers.Dense(
                hparams.gru_unit,
                bias_initializer=keras.initializers.Zeros(),
                kernel_initializer=keras.initializers.glorot_uniform(seed=self.seed),
            )(user_present)

        model = keras.Model(his_input_title_body_verts, user_present, name="user_encoder")
        return model

    def _build_newsencoder(self, embedding_layer):
        """The main function to create news encoder.

        Args:
            embedding_layer(obj): a word embedding layer.
        
        Return:
            obj: the news encoder.
        """
        hparams = self.hparams
        input_title_body_verts = keras.Input(
            shape=(hparams.title_size + hparams.body_size + 2,), dtype="int32"
        )
        sequences_input_title = layers.Lambda(lambda x: x[:, : hparams.title_size])(
            input_title_body_verts
        )
        sequences_input_body = layers.Lambda(
            lambda x: x[:, hparams.title_size : hparams.title_size + hparams.body_size]
        )(input_title_body_verts)
        input_vert = layers.Lambda(
            lambda x: x[
                :,
                hparams.title_size
                + hparams.body_size : hparams.title_size
                + hparams.body_size
                + 1,
            ]
        )(input_title_body_verts)
        input_subvert = layers.Lambda(
            lambda x: x[:, hparams.title_size + hparams.body_size + 1 :]
        )(input_title_body_verts)

        title_repr = self._build_titleencoder(embedding_layer)(sequences_input_title)
        body_repr = self._build_bodyencoder(embedding_layer)(sequences_input_body)
        vert_repr = self._build_vertencoder()(input_vert)
        subvert_repr = self._build_subvertencoder()(input_subvert)

        concate_repr = layers.Concatenate(axis=-2)(
            [title_repr, body_repr, vert_repr, subvert_repr]
        )

        y = layers.Dropout(hparams.dropout)(concate_repr)
        y = SelfAttention(hparams.head_num, hparams.head_dim, seed=self.seed)([y, y, y])
        y = layers.Dropout(hparams.dropout)(y)
        news_repr = AttLayer2(hparams.attention_hidden_dim, seed=self.seed)(y)
        
        model = keras.Model(input_title_body_verts, news_repr, name="news_encoder")
        return model

    #build 各部分(multi-view)
    def _build_titleencoder(self, embedding_layer):
        """build title encoder of news encoder.

        Args:
            embedding_layer(obj): a word embedding layer.
        
        Return:
            obj: the title encoder.
        """
        hparams = self.hparams
        sequences_input_title = keras.Input(shape=(hparams.title_size,), dtype="int32")
        embedded_sequences_title = embedding_layer(sequences_input_title)

        y = layers.Dropout(hparams.dropout)(embedded_sequences_title)
        y = layers.Conv1D(
            hparams.filter_num,
            hparams.window_size,
            activation=hparams.cnn_activation,
            padding="same",
            bias_initializer=keras.initializers.Zeros(),
            kernel_initializer=keras.initializers.glorot_uniform(seed=self.seed),
        )(y)
        y = layers.Dropout(hparams.dropout)(y)
        pred_title = AttLayer2(hparams.attention_hidden_dim, seed=self.seed)(y)
        pred_title = layers.Reshape((1, hparams.filter_num))(pred_title)

        model = keras.Model(sequences_input_title, pred_title, name="title_encoder")
        return model

    def _build_bodyencoder(self, embedding_layer):
        """build body encoder of news encoder.

        Args:
            embedding_layer(obj): a word embedding layer.
        
        Return:
            obj: the body encoder.
        """
        hparams = self.hparams
        sequences_input_body = keras.Input(shape=(hparams.body_size,), dtype="int32")
        embedded_sequences_body = embedding_layer(sequences_input_body)

        y = layers.Dropout(hparams.dropout)(embedded_sequences_body)
        y = layers.Conv1D(
            hparams.filter_num,
            hparams.window_size,
            activation=hparams.cnn_activation,
            padding="same",
            bias_initializer=keras.initializers.Zeros(),
            kernel_initializer=keras.initializers.glorot_uniform(seed=self.seed),
        )(y)
        y = layers.Dropout(hparams.dropout)(y)
        pred_body = AttLayer2(hparams.attention_hidden_dim, seed=self.seed)(y)
        pred_body = layers.Reshape((1, hparams.filter_num))(pred_body)

        model = keras.Model(sequences_input_body, pred_body, name="body_encoder")
        return model

    def _build_vertencoder(self):
        """build vert encoder of news encoder.

        Return:
            obj: the vert encoder.
        """
        hparams = self.hparams
        input_vert = keras.Input(shape=(1,), dtype="int32")

        vert_embedding = layers.Embedding(
            hparams.vert_num, hparams.vert_emb_dim, trainable=True
        )

        vert_emb = vert_embedding(input_vert)
        pred_vert = layers.Dense(
            hparams.filter_num,
            activation=hparams.dense_activation,
            bias_initializer=keras.initializers.Zeros(),
            kernel_initializer=keras.initializers.glorot_uniform(seed=self.seed),
        )(vert_emb)
        pred_vert = layers.Reshape((1, hparams.filter_num))(pred_vert)

        model = keras.Model(input_vert, pred_vert, name="vert_encoder")
        return model

    def _build_subvertencoder(self):
        """build subvert encoder of news encoder.

        Return:
            obj: the subvert encoder.
        """
        hparams = self.hparams
        input_subvert = keras.Input(shape=(1,), dtype="int32")

        subvert_embedding = layers.Embedding(
            hparams.subvert_num, hparams.subvert_emb_dim, trainable=True
        )

        subvert_emb = subvert_embedding(input_subvert)
        pred_subvert = layers.Dense(
            hparams.filter_num,
            activation=hparams.dense_activation,
            bias_initializer=keras.initializers.Zeros(),
            kernel_initializer=keras.initializers.glorot_uniform(seed=self.seed),
        )(subvert_emb)
        pred_subvert = layers.Reshape((1, hparams.filter_num))(pred_subvert)

        model = keras.Model(input_subvert, pred_subvert, name="subvert_encoder")
        return model



    def _build_model(self):
        """The main function to create Model's logic. The core
        is a user encoder and a news encoder.
        
        Returns:
            obj: a model used to train.
            obj: a model used to evaluate and inference.
        """
        hparams = self.hparams

        his_input_title = keras.Input(
            shape=(hparams.his_size, hparams.title_size), dtype="int32"
        )

        his_input_body = keras.Input(
            shape=(hparams.his_size, hparams.body_size), dtype="int32"
        )
        his_input_vert = keras.Input(shape=(hparams.his_size, 1), dtype="int32")
        his_input_subvert = keras.Input(shape=(hparams.his_size, 1), dtype="int32")


        pred_input_title = keras.Input(
            shape=(hparams.npratio + 1, hparams.title_size), dtype="int32"
        )
        pred_input_body = keras.Input(
            shape=(hparams.npratio + 1, hparams.body_size), dtype="int32"
        )
        pred_input_vert = keras.Input(shape=(hparams.npratio + 1, 1), dtype="int32")
        pred_input_subvert = keras.Input(shape=(hparams.npratio + 1, 1), dtype="int32")


        pred_input_title_one = keras.Input(
            shape=(1, hparams.title_size,), dtype="int32"
        )
        pred_input_body_one = keras.Input(shape=(1, hparams.body_size,), dtype="int32")
        pred_input_vert_one = keras.Input(shape=(1, 1), dtype="int32")
        pred_input_subvert_one = keras.Input(shape=(1, 1), dtype="int32")

        his_title_body_verts = layers.Concatenate(axis=-1)(
            [his_input_title, his_input_body, his_input_vert, his_input_subvert]
        )

        pred_title_body_verts = layers.Concatenate(axis=-1)(
            [pred_input_title, pred_input_body, pred_input_vert, pred_input_subvert]
        )

        pred_title_body_verts_one = layers.Concatenate(axis=-1)(
            [
                pred_input_title_one,
                pred_input_body_one,
                pred_input_vert_one,
                pred_input_subvert_one,
            ]
        )
        pred_title_body_verts_one = layers.Reshape((-1,))(pred_title_body_verts_one)


        embedding_layer = layers.Embedding(
            self.word2vec_embedding.shape[0],
            hparams.word_emb_dim,
            weights=[self.word2vec_embedding],
            trainable=True,
        )

        self.newsencoder = self._build_newsencoder(embedding_layer)
        self.userencoder = self._build_userencoder(self.newsencoder)

        user_present = self.userencoder(his_title_body_verts)
        news_present = layers.TimeDistributed(self.newsencoder)(pred_title_body_verts)
        news_present_one = self.newsencoder(pred_title_body_verts_one)

        preds = layers.Dot(axes=-1)([news_present, user_present])
        preds = layers.Activation(activation="softmax")(preds)

        pred_one = layers.Dot(axes=-1)([news_present_one, user_present])
        pred_one = layers.Activation(activation="sigmoid")(pred_one)

        model = keras.Model([his_input_title,his_input_body,
                his_input_vert,
                his_input_subvert,
                pred_input_title,
                pred_input_body,
                pred_input_vert,
                pred_input_subvert,], preds)
        scorer = keras.Model([his_input_title, his_input_body,
                his_input_vert,
                his_input_subvert,
                pred_input_title_one,
                pred_input_body_one,
                pred_input_vert_one,
                pred_input_subvert_one,], pred_one)

        return model, scorer
