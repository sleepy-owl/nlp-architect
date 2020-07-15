import math
from torch import nn
import torch
from torch.nn import CrossEntropyLoss
from transformers.modeling_bert import BertEncoder, BertLayer, \
        BertAttention, BertSelfAttention, BertSelfOutput, BertConfig
from transformers import BertForTokenClassification, BertModel

class SaBertConfig(BertConfig):
    def __init__(self, **kwargs):
        super().__init__()
        self.li_layer: int
        self.replace_final: list
        self.random_init: list
        self.all_layers: list
        self.duplicated_rels: list
        self.transpose: list
        self.layers_range: list

    def add_extra_args(self, hparams):
        self.li_layer = hparams.li_layer
        self.replace_final = hparams.replace_final
        self.random_init = hparams.random_init
        self.all_layers = hparams.all_layers
        self.duplicated_rels = hparams.duplicated_rels
        self.transpose = hparams.transpose
        self.layers_range = hparams.layers_range

class SaBertForToken(BertForTokenClassification):
    """BERT token classification head with linear classifier.

       The forward requires an additional 'valid_ids' map that maps the tensors
       for valid tokens (e.g., ignores additional word piece tokens generated by
       the tokenizer, as in NER task the 'X' label).
    """

    def __init__(self, config):
        super(SaBertForToken, self).__init__(config)
        self.bert = SaBertExtModel(config)

    # def from_pretrained(self.model_name_or_path, from_tf=bool(
    #         '.ckpt' in self.model_name_or_path), config=self.config):

    #     return super.from_pretrained(self.model_name_or_path, from_tf=bool(
    #         '.ckpt' in self.model_name_or_path), config=self.config)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None, labels=None,
                position_ids=None, head_mask=None, valid_ids=None, head_probs=None):
        outputs = self.bert(
            input_ids,
            token_type_ids=token_type_ids,
            attention_mask=attention_mask,
            head_mask=head_mask,
            head_probs=head_probs)
        sequence_output = outputs[0]
        sequence_output = self.dropout(sequence_output)
        logits = self.classifier(sequence_output)

        if labels is not None:
            loss_fct = CrossEntropyLoss(ignore_index=0)
            active_positions = valid_ids.view(-1) != 0.0
            active_labels = labels.view(-1)[active_positions]
            active_logits = logits.view(-1, self.num_labels)[active_positions]
            loss = loss_fct(active_logits, active_labels)
            return (loss, logits, labels)
        return (logits,)

class SaBertExtModel(BertModel):
    def __init__(self, config):
        super(SaBertExtModel, self).__init__(config)
        self.encoder = SaBertExtEncoder(config)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None, position_ids=None, head_mask=None,
                head_probs=None):
        if attention_mask is None:
            attention_mask = torch.ones_like(input_ids)
        if token_type_ids is None:
            token_type_ids = torch.zeros_like(input_ids)

        # We create a 3D attention mask from a 2D tensor mask.
        # Sizes are [batch_size, 1, 1, to_seq_length]
        # So we can broadcast to [batch_size, num_heads, from_seq_length, to_seq_length]
        # this attention mask is more simple than the triangular masking of causal attention
        # used in OpenAI GPT, we just need to prepare the broadcast dimension here.
        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)

        # Since attention_mask is 1.0 for positions we want to attend and 0.0 for
        # masked positions, this operation will create a tensor which is 0.0 for
        # positions we want to attend and -10000.0 for masked positions.
        # Since we are adding it to the raw scores before the softmax, this is
        # effectively the same as removing these entirely.
        extended_attention_mask = extended_attention_mask.to(dtype=next(self.parameters()).dtype) # fp16 compatibility
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0

        # Prepare head mask if needed
        # 1.0 in head_mask indicate we keep the head
        # attention_probs has shape bsz x n_heads x N x N
        # input head_mask has shape [num_heads] or [num_hidden_layers x num_heads]
        # and head_mask is converted to shape [num_hidden_layers x batch x num_heads x seq_length x seq_length]
        if head_mask is not None:
            if head_mask.dim() == 1:
                head_mask = head_mask.unsqueeze(0).unsqueeze(0).unsqueeze(-1).unsqueeze(-1)
                head_mask = head_mask.expand(self.config.num_hidden_layers, -1, -1, -1, -1)
            elif head_mask.dim() == 2:
                head_mask = head_mask.unsqueeze(1).unsqueeze(-1).unsqueeze(-1)  # We can specify head_mask for each layer
            head_mask = head_mask.to(dtype=next(self.parameters()).dtype) # switch to fload if need + fp16 compatibility
        else:
            head_mask = [None] * self.config.num_hidden_layers

        embedding_output = self.embeddings(input_ids, position_ids=position_ids, token_type_ids=token_type_ids)
        encoder_outputs = self.encoder(embedding_output,
                                       extended_attention_mask,
                                       head_mask=head_mask,
                                       head_probs=head_probs)
        sequence_output = encoder_outputs[0]
        pooled_output = self.pooler(sequence_output)

        outputs = (sequence_output, pooled_output,) + encoder_outputs[1:]  # add hidden_states and attentions if they are here
        return outputs  # sequence_output, pooled_output, (hidden_states), (attentions)

class SaBertExtEncoder(BertEncoder):
    def __init__(self, config):
        super(SaBertExtEncoder, self).__init__(config)
        self.layer = nn.ModuleList([SaBertExtLayer(config, layer_num) for \
            layer_num in range(config.num_hidden_layers)])
        self.li_layer = config.li_layer
        self.all_layers = config.all_layers
        self.layers_range = config.layers_range

    # def forward(
    #     self,
    #     hidden_states,
    #     attention_mask=None,
    #     head_mask=None,
    #     encoder_hidden_states=None,
    #     encoder_attention_mask=None,
    #     output_attentions=False,
    #     output_hidden_states=False,
    # ):
    #     all_hidden_states = ()
    #     all_attentions = ()
    #     for i, layer_module in enumerate(self.layer):
    #         if output_hidden_states:
    #             all_hidden_states = all_hidden_states + (hidden_states,)

    #         if getattr(self.config, "gradient_checkpointing", False):

    #             def create_custom_forward(module):
    #                 def custom_forward(*inputs):
    #                     return module(*inputs, output_attentions)

    #                 return custom_forward

    #             layer_outputs = torch.utils.checkpoint.checkpoint(
    #                 create_custom_forward(layer_module),
    #                 hidden_states,
    #                 attention_mask,
    #                 head_mask[i],
    #                 encoder_hidden_states,
    #                 encoder_attention_mask,
    #             )
    #         else:
    #             layer_outputs = layer_module(
    #                 hidden_states,
    #                 attention_mask,
    #                 head_mask[i],
    #                 encoder_hidden_states,
    #                 encoder_attention_mask,
    #                 output_attentions,
    #             )
    #         hidden_states = layer_outputs[0]

    #         if output_attentions:
    #             all_attentions = all_attentions + (layer_outputs[1],)

    #     # Add last layer
    #     if output_hidden_states:
    #         all_hidden_states = all_hidden_states + (hidden_states,)

    #     outputs = (hidden_states,)
    #     if output_hidden_states:
    #         outputs = outputs + (all_hidden_states,)
    #     if output_attentions:
    #         outputs = outputs + (all_attentions,)
    #     return outputs  # last-layer hidden state, (all hidden states), (all attentions)


    def forward(self, hidden_states, attention_mask, head_mask=None, head_probs=None):
        all_hidden_states = ()
        all_attentions = ()
        for i, layer_module in enumerate(self.layer):
            if self.output_hidden_states:
                all_hidden_states = all_hidden_states + (hidden_states,)

            head_probs_layer = None
            if self.all_layers is True or i == self.li_layer or i in self.layers_range:
                head_probs_layer = head_probs


            layer_outputs = layer_module(hidden_states, attention_mask, head_mask[i], 
                        head_probs_layer)
            
            hidden_states = layer_outputs[0]

            if self.output_attentions:
                all_attentions = all_attentions + (layer_outputs[1],)

        # Add last layer
        if self.output_hidden_states:
            all_hidden_states = all_hidden_states + (hidden_states,)

        outputs = (hidden_states,)
        if self.output_hidden_states:
            outputs = outputs + (all_hidden_states,)
        if self.output_attentions:
            outputs = outputs + (all_attentions,)
        return outputs  # outputs, (hidden states), (attentions)

class SaBertExtLayer(BertLayer):
    def __init__(self, config, layer_num):
        super(SaBertExtLayer, self).__init__(config)
        self.attention = SaBertExtAttention(config, layer_num)

    def forward(self, hidden_states, attention_mask, head_mask=None, head_probs=None):
        attention_outputs = self.attention(hidden_states, attention_mask, head_mask, head_probs)
        attention_output = attention_outputs[0]
        intermediate_output = self.intermediate(attention_output)
        layer_output = self.output(intermediate_output, attention_output)
        outputs = (layer_output,) + attention_outputs[1:]  # add attentions if we output them
        return outputs

class SaBertExtAttention(BertAttention):
    def __init__(self, config, layer_num):
        super(SaBertExtAttention, self).__init__(config)
        self.self = SaBertExtSelfAttention(config, layer_num)
        self.output = SaBertExtSelfOutput(config, layer_num)

    def forward(self, input_tensor, attention_mask, head_mask=None, head_probs=None):
        self_outputs = self.self(input_tensor, attention_mask, head_mask, head_probs)
        attention_output = self.output(self_outputs[0], input_tensor, head_probs)
        outputs = (attention_output,) + self_outputs[1:]  # add attentions if we output them
        return outputs

class SaBertExtSelfAttention(BertSelfAttention):
    def __init__(self, config, layer_num):
        super(SaBertExtSelfAttention, self).__init__(config)
        self.orig_num_attention_heads = config.num_attention_heads
        self.replace_final = config.replace_final
        self.random_init = config.random_init
        self.duplicated_rels = config.duplicated_rels
        self.transpose = config.transpose

        #self.attention_head_size = int(config.hidden_size / config.num_attention_heads)
        if  (layer_num == config.li_layer or config.all_layers is True \
             or layer_num in config.layers_range):
            self.num_attention_heads = 13           
            self.extra_query = nn.Linear(config.hidden_size, self.attention_head_size)
            self.extra_key = nn.Linear(config.hidden_size, self.attention_head_size)
            self.extra_value = nn.Linear(config.hidden_size, self.attention_head_size)
            nn.init.normal_(self.extra_key.weight.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_key.bias.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_query.weight.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_query.bias.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_value.weight.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_value.bias.data, mean=0, std=0.02)

    def forward(self, hidden_states, attention_mask, head_mask=None, head_probs=None):
        
        if head_probs is not None:
            self.all_head_size = self.num_attention_heads * self.attention_head_size            
            mixed_query_layer = torch.cat((self.query(hidden_states), self.extra_query(hidden_states)),2)
            mixed_key_layer = torch.cat((self.key(hidden_states), self.extra_key(hidden_states)),2)
            mixed_value_layer = torch.cat((self.value(hidden_states), self.extra_value(hidden_states)),2)
  
        else:
            self.all_head_size = self.num_attention_heads * self.attention_head_size
            mixed_query_layer = self.query(hidden_states)
            mixed_key_layer = self.key(hidden_states)
            mixed_value_layer = self.value(hidden_states)

        query_layer = self.transpose_for_scores(mixed_query_layer)
        key_layer = self.transpose_for_scores(mixed_key_layer)
        value_layer = self.transpose_for_scores(mixed_value_layer)

        attention_scores = torch.matmul(query_layer, key_layer.transpose(-1, -2))        

        if head_probs is not None and self.random_init is False:   

            #  duplicated heads across all matrix (one vector duplicated across matrix)
            if self.duplicated_rels is True:
                head_probs = head_probs.sum(1, keepdim=True)
                # duplicate sum vector
                head_probs = head_probs.repeat(1,64,1)
                 
            head_probs_norm = head_probs / head_probs.max(2, keepdim=True)[0]
            head_probs_norm[torch.isnan(head_probs_norm)] = 0
            
           # _, indices = head_probs_norm.max(2)
           # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
           # ex_head_attention_probs = torch.zeros(head_probs_norm.shape).to(device)
            
           # for batch, tokens in enumerate(indices):
           #     mask_matrix = torch.zeros([head_probs_norm.shape[1], head_probs_norm.shape[2]])
           #     i=0
           #     for token in tokens:
           #         if token != 0:
           #             mask_matrix[i][token] = 1.                        
           #             i=i+1                        

            #    ex_head_attention_probs[batch] = mask_matrix         

            #if self.duplicated_rels is True:
            #    head_probs_norm = ex_head_attention_probs

            original_12head_attn_scores = attention_scores[:, :self.orig_num_attention_heads]
            original_12head_attn_scores = original_12head_attn_scores / math.sqrt(self.attention_head_size)
            original_12head_attn_scores = original_12head_attn_scores + attention_mask
            original_12head_attn_probs = nn.Softmax(dim=-1)(original_12head_attn_scores)

            extra_head_attn = attention_scores[:,self.orig_num_attention_heads,:,:] 
            head_probs_norm = head_probs_norm*8+ attention_mask.squeeze(1)
            
                    
            if not self.replace_final: 
                if self.transpose == True:                
                    head_probs_norm = head_probs_norm.transpose(-1, -2)                   

                extra_head_scaled_attn = ((extra_head_attn *8) * head_probs_norm).unsqueeze(1)       
                extra_head_scaled_attn = extra_head_scaled_attn + attention_mask
                extra_head_scaled_attn_probs = nn.Softmax(dim=-1)(extra_head_scaled_attn)
                attention_probs = torch.cat((original_12head_attn_probs, extra_head_scaled_attn_probs), 1)
           
            # else:
            #     attention_probs = torch.cat((original_12head_attn_probs, ex_head_attention_probs.unsqueeze(1)),1)

        if head_probs is None or self.random_init is True:
            attention_scores = attention_scores / math.sqrt(self.attention_head_size)
            # Apply the attention mask is (precomputed for all layers in BertModel forward() function)
            attention_scores = attention_scores + attention_mask

            # Normalize the attention scores to probabilities.
            attention_probs = nn.Softmax(dim=-1)(attention_scores)
            
        # This is actually dropping out entire tokens to attend to, which might
        # seem a bit unusual, but is taken from the original Transformer paper.
        attention_probs = self.dropout(attention_probs)

        # Mask heads if we want to
        if head_mask is not None:
            attention_probs = attention_probs * head_mask

        context_layer = torch.matmul(attention_probs, value_layer)

        context_layer = context_layer.permute(0, 2, 1, 3).contiguous()
        new_context_layer_shape = context_layer.size()[:-2] + (self.all_head_size,)
        context_layer = context_layer.view(*new_context_layer_shape)

        outputs = (context_layer, attention_probs) if self.output_attentions else (context_layer,)
        return outputs

class SaBertExtSelfOutput(BertSelfOutput):
    def __init__(self, config, layer_num):
        super(SaBertExtSelfOutput, self).__init__(config)
        if  (layer_num == config.li_layer or config.all_layers is True \
             or layer_num in config.layers_range): 
            self.original_num_attention_heads = config.num_attention_heads
            self.attention_head_size = int(config.hidden_size / self.original_num_attention_heads)
            self.dense_extra_head = nn.Linear(self.attention_head_size, config.hidden_size)

    def forward(self, hidden_states, input_tensor, head_probs=None):
        if head_probs is not None:
            original_hidden_vec_size = self.original_num_attention_heads*self.attention_head_size
            hidden_states = self.dense(hidden_states[:,:,:original_hidden_vec_size]) + \
                self.dense_extra_head(hidden_states[:,:,original_hidden_vec_size:])
        else:
            hidden_states = self.dense(hidden_states)
        
        hidden_states = self.dropout(hidden_states)
        hidden_states = self.LayerNorm(hidden_states + input_tensor)
        return hidden_states
