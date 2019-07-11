import torch
import torch.nn as nn
import torch.nn.functional as F


class LSTMEncoder(nn.Module):
    def __init__(self, vocab, hidden_size, n_layers, dropout=0.1):
        super(LSTMEncoder, self).__init__()
        self.n_layers = n_layers
        self.hidden_size = hidden_size

        self.embed = nn.Embedding.from_pretrained(vocab.vectors)
        self.lstm = nn.LSTM(vocab.dim, hidden_size, n_layers,
                            dropout=(0 if n_layers == 1 else dropout), bidirectional=True)
        self.linear = nn.Linear(2 * n_layers * hidden_size, hidden_size)

    def forward(self, x, input_lengths, hidden=None):
        embedded = self.embed(x)
        embedded = torch.transpose(embedded, 0, 1)
        packed = nn.utils.rnn.pack_padded_sequence(embedded, input_lengths)
        outputs, (hidden, _) = self.lstm(packed, hidden)
        batch_size = hidden.shape[1]
        hidden = torch.transpose(hidden, 0, 1).contiguous().view(batch_size, -1)
        hidden = self.linear(hidden)
        return F.normalize(hidden)
