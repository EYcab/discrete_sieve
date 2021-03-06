"""Information Sieve

Greg Ver Steeg and Aram Galstyan. "The Information Sieve"
ICML 2016. http://arxiv.org/abs/1507.02284

Code below written by:
Greg Ver Steeg (gregv@isi.edu), 2015.
"""

import numpy as np  # Tested with 1.8.0
import corex as ce
import remainder as re


class Sieve(object):
    """
    Information Sieve

    Iteratively learn a series of latent factors that are maximally
    informative about the data. Negative values are treated as missing.

    An extension of ideas in this paper:
    Greg Ver Steeg and Aram Galstyan. "Maximally Informative
    Hierarchical Representations of High-Dimensional Data"
    AISTATS, 2015. arXiv preprint arXiv:1410.7404.

    Code follows sklearn naming/style (e.g. fit(X) to train)

    Parameters
    ----------
    max_layers : int, optional
        Maximum number of latent factors before ending.

    batch_size : int, optional
        Number of examples per minibatch. NOT IMPLEMENTED IN THIS VERSION.

    verbose : int, optional
        The verbosity level. The default, zero, means silent mode. 1 outputs TC(X;Y) as you go
        2 output alpha matrix and MIs as you go.

    seed : integer or numpy.RandomState, optional
        A random number generator instance to define the state of the
        random permutations generator. If an integer is given, it fixes the
        seed. Defaults to the global numpy random number generator.

    Attributes
    ----------
    y_series : array, [max_layers or until convergence]
        A list of CorEx objects for each latent factor.

    References
    ----------

    [1]     Greg Ver Steeg and Aram Galstyan. "Discovering Structure in
            High-Dimensional Data Through Correlation Explanation."
            NIPS, 2014. arxiv preprint arXiv:1406.1222.

    [2]     Greg Ver Steeg and Aram Galstyan. "Maximally Informative
            Hierarchical Representations of High-Dimensional Data"
            AISTATS, 2015. arXiv preprint arXiv:1410.7404.

    [3]     Greg Ver Steeg and Aram Galstyan. "The Information Sieve"
            ICML 2016.

    """

    def __init__(self, max_layers=10, data_format='default', **kwargs):
        self.max_layers = max_layers
        self.data_format = data_format
        self.kwargs = kwargs
        self.verbose = kwargs.get('verbose', False)
        self.layers = []
        self.x_stats = []

    @property
    def labels(self):
        """Maximum likelihood labels for training data. Can access with self.labels (no parens needed)"""
        # return np.array([_label(layer.corex.p_y_given_x) for layer in self.layers]).T
        return np.array([layer.labels for layer in self.layers]).T

    @property
    def clusters(self):
        """ Return hard cluster assignments, calculated from soft relations in self.mis."""
        return np.argmax(self.mis, axis=0)

    @property
    def tcs(self):
        """Maximum likelihood labels for training data. Can access with self.labels (no parens needed)"""
        return [layer.corex.tc for layer in self.layers]

    @property
    def tc(self):
        return np.sum(self.tcs)

    @property
    def lb(self):
        """Remainder information to be subtracted from estimate of TC."""
        return sum(layer.lb for layer in self.layers)

    @property
    def ub(self):
        """Remainder information to be subtracted from estimate of TC."""
        return sum(layer.ub for layer in self.layers)

    @property
    def mis(self):
        """ A matrix of mutual information MI(Y_j: X_i). MI is computed wrt to original data """
        mis = self.layers[0].corex.mis[np.newaxis, :]
        for layer in self.layers[1:]:
            mis = np.vstack([np.hstack([mis, np.zeros((mis.shape[0], 1))]), layer.corex.mis])
        return mis

    def transform(self, x, layer=0, prev_labels=None):
        # Transform data into hidden factors + remainder info
        # Returns a tuple (x_remainder, labels) which is the remainder info at the last layer and all the labels.
        x_out = self.layers[layer].transform(x)
        if prev_labels is None:
            labels = x_out[:, -1:]
        else:
            labels = np.hstack([prev_labels, x_out[:, -1:]])

        if layer == len(self.layers) - 1:
            return x_out, labels
        else:
            return self.transform(x_out, layer=layer+1, prev_labels=labels)

    def invert(self, x, layer=None):
        """From remainder info and labels, reconstruct input."""
        if layer is None:
            layer = len(self.layers) - 1

        if layer == 0:
            return self.layers[layer].invert(x)
        else:
            return self.invert(self.layers[layer].invert(x), layer=layer-1)

    def predict(self, y):
        return np.array([self.predict_variable(y, i) for i in range(self.n_variables)]).T

    def predict_variable(self, ys, i):
        most_likely = []
        for y in ys:
            prediction = np.zeros(100)  # TODO: pick correct number (keep x_stats for all levels?)
            for xi, nxi in enumerate(self.x_stats[i]):
                x_pred = self.invert_variable(xi, y, i)
                prediction[x_pred] += nxi
            most_likely.append(np.argmax(prediction))
        return np.array(most_likely)

    def invert_variable(self, zi, y, i):
        for k, layer in reversed(list(enumerate(self.layers))):
            zi = layer.remainders[i].predict([y[k]], [zi])[0]
        return zi

    def fit(self, x):
        n_samples, self.n_variables = x.shape

        while len(self.layers) < self.max_layers:
            next_layer = SieveLayer(x, **self.kwargs)
            x = next_layer.transform(x)
            if self.verbose:
                print 'tc: %0.3f, (+) %0.3f, (-) %0.3f' % (next_layer.corex.tc, next_layer.ub, next_layer.lb)
            #if next_layer.corex.tc - 2 * next_layer.ub - next_layer.lb > 1. / n_samples:  # Lower bound still increasing
            if next_layer.corex.tc - next_layer.lb > 1. / n_samples:  # Lower bound still increasing
                self.layers.append(next_layer)
                self.x_stats = [np.bincount(x[x[:, i] >= 0, i]) for i in range(self.n_variables)]
            else:
                break

        if self.verbose:
            print ['tc: %0.3f (-) %0.3f (+) %0.3f' % (layer.corex.tc, layer.lb, layer.ub) for layer in self.layers]
        return self


class SieveLayer(object):
    """
    A single layer of the information sieve

    Parameters
    ----------
    x: array of training data
    kwargs: arguments to pass along to CorEx

    Attributes
    ----------
    corex : A CorEx object
    remainders : A list of remainder objects (includes transform, predict and bound info)
    labels : the output labels from CorEx

    """
    def __init__(self, x, **kwargs):
        k_max = kwargs.pop('k_max', 2)  # Sets max cardinality for Remainder objects
        self.verbose = kwargs.get('verbose', False)
        self.corex = ce.Corex(**kwargs).fit(x)
        self.labels = self.corex.labels
        self.remainders = [re.Remainder(xs[xs >= 0], self.labels[xs >= 0], k_max=k_max) for xs in x.T]
        if self.verbose:
            print 'z cardinalities', [r.pz_xy.shape[0] for r in self.remainders]

    # These functions define the transformation and prediction. In principle, many alternatives could be tried.
    # But we have chosen to minimize the gap between upper and lower bounds.
    def transform(self, x):
        """Transform data into hidden factors + remainder info"""
        labels = self.corex.transform(x)
        xr = np.array([self.remainders[i].transform(x[:, i], labels) for i in range(x.shape[1])]).T
        return np.hstack([xr, labels[:, np.newaxis]])

    def invert(self, xr):
        """Recover x from y and remainder information."""
        ys = xr[:, -1]
        x = np.array([self.remainders[i].predict(ys, xr[:, i]) for i in range(xr.shape[1] - 1)]).T
        return x

    @property
    def lb(self):
        """Lower bound on TC."""
        return sum(r.mi for r in self.remainders)

    @property
    def ub(self):
        """Upper bound on TC."""
        return sum(2 * r.h for r in self.remainders)