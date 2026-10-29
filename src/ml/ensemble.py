"""ensemble.py - a small bag of gradient-boosted tree models whose
probabilities are averaged. Averaging several models trained with different
random feature subsets makes the very-high-confidence tail (the only part the
bot trades) less noisy. Lives in its own module so joblib can unpickle it."""
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier


class BaggedHGB:
    def __init__(self, n=3, **kw):
        self.n, self.kw = n, kw
        self.models = []

    def fit(self, X, y):
        self.models = [HistGradientBoostingClassifier(random_state=i, max_features=0.6 if self.n > 1 else 1.0, **self.kw).fit(X, y)
                       for i in range(self.n)]
        self.classes_ = self.models[0].classes_
        return self

    def predict_proba(self, X):
        return np.mean([m.predict_proba(X) for m in self.models], axis=0)

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)
