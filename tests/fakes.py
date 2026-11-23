"""테스트 대역: 외부 모델 없이 결정적으로 동작하는 임베딩. 실제 모델 품질 검증이 아니다."""
import hashlib
import math
import re
import string

from tokenizers import Tokenizer, models, pre_tokenizers, processors
from transformers import PreTrainedTokenizerFast


def local_tokenizer(text):
    symbols = list(dict.fromkeys("[UNK] [CLS] [SEP]".split() + list(set(text + string.ascii_letters))))
    symbols += ["##" + c for c in symbols if len(c) == 1]
    backend = Tokenizer(models.WordPiece({token: idx for idx, token in enumerate(symbols)}, unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    backend.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)])
    return PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]")


class FakeEmbeddings:
    """문자 2-gram 해시 임베딩(384차원, 정규화). 문자가 겹치는 텍스트가 가깝다."""

    model_name = "fake-char-bigram-384"

    def __init__(self, corpus: str):
        self.tokenizer = local_tokenizer(corpus)
        self.calls = []
        self.fail_on = None

    def embed_documents(self, texts):
        self.calls.append(len(texts))
        if self.fail_on and any(self.fail_on in text for text in texts):
            raise RuntimeError("임베딩 실패(테스트)")
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        vec = [0.0] * 384
        compact = re.sub(r"\s+", "", text)
        for index in range(max(1, len(compact) - 1)):
            gram = compact[index:index + 2]
            vec[int(hashlib.md5(gram.encode()).hexdigest(), 16) % 384] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
