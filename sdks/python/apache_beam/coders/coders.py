#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Collection of useful coders."""

import base64
import cPickle as pickle
import google.protobuf

from apache_beam.coders import coder_impl
from apache_beam.utils import urns
from apache_beam.utils import proto_utils

# pylint: disable=wrong-import-order, wrong-import-position, ungrouped-imports
try:
  from stream import get_varint_size
except ImportError:
  from slow_stream import get_varint_size
# pylint: enable=wrong-import-order, wrong-import-position, ungrouped-imports


# pylint: disable=wrong-import-order, wrong-import-position
# Avoid dependencies on the full SDK.
try:
  # Import dill from the pickler module to make sure our monkey-patching of dill
  # occurs.
  from apache_beam.internal.pickler import dill
except ImportError:
  # We fall back to using the stock dill library in tests that don't use the
  # full Python SDK.
  import dill


def serialize_coder(coder):
  from apache_beam.internal import pickler
  return '%s$%s' % (coder.__class__.__name__, pickler.dumps(coder))


def deserialize_coder(serialized):
  from apache_beam.internal import pickler
  return pickler.loads(serialized.split('$', 1)[1])
# pylint: enable=wrong-import-order, wrong-import-position


class Coder(object):
  """Base class for coders."""

  def encode(self, value):
    """Encodes the given object into a byte string."""
    raise NotImplementedError('Encode not implemented: %s.' % self)

  def decode(self, encoded):
    """Decodes the given byte string into the corresponding object."""
    raise NotImplementedError('Decode not implemented: %s.' % self)

  def is_deterministic(self):
    """Whether this coder is guaranteed to encode values deterministically.

    A deterministic coder is required for key coders in GroupByKey operations
    to produce consistent results.

    For example, note that the default coder, the PickleCoder, is not
    deterministic: the ordering of picked entries in maps may vary across
    executions since there is no defined order, and such a coder is not in
    general suitable for usage as a key coder in GroupByKey operations, since
    each instance of the same key may be encoded differently.

    Returns:
      Whether coder is deterministic.
    """
    return False

  def estimate_size(self, value):
    """Estimates the encoded size of the given value, in bytes.

    Dataflow estimates the encoded size of a PCollection processed in a pipeline
    step by using the estimated size of a random sample of elements in that
    PCollection.

    The default implementation encodes the given value and returns its byte
    size.  If a coder can provide a fast estimate of the encoded size of a value
    (e.g., if the encoding has a fixed size), it can provide its estimate here
    to improve performance.

    Arguments:
      value: the value whose encoded size is to be estimated.

    Returns:
      The estimated encoded size of the given value.
    """
    return len(self.encode(value))

  # ===========================================================================
  # Methods below are internal SDK details that don't need to be modified for
  # user-defined coders.
  # ===========================================================================

  def _create_impl(self):
    """Creates a CoderImpl to do the actual encoding and decoding.
    """
    return coder_impl.CallbackCoderImpl(self.encode, self.decode,
                                        self.estimate_size)

  def get_impl(self):
    if not hasattr(self, '_impl'):
      self._impl = self._create_impl()
      assert isinstance(self._impl, coder_impl.CoderImpl)
    return self._impl

  def __getstate__(self):
    return self._dict_without_impl()

  def _dict_without_impl(self):
    if hasattr(self, '_impl'):
      d = dict(self.__dict__)
      del d['_impl']
      return d
    return self.__dict__

  @classmethod
  def from_type_hint(cls, unused_typehint, unused_registry):
    # If not overridden, just construct the coder without arguments.
    return cls()

  def is_kv_coder(self):
    return False

  def key_coder(self):
    if self.is_kv_coder():
      raise NotImplementedError('key_coder: %s' % self)
    else:
      raise ValueError('Not a KV coder: %s.' % self)

  def value_coder(self):
    if self.is_kv_coder():
      raise NotImplementedError('value_coder: %s' % self)
    else:
      raise ValueError('Not a KV coder: %s.' % self)

  def _get_component_coders(self):
    """Returns the internal component coders of this coder."""
    # This is an internal detail of the Coder API and does not need to be
    # refined in user-defined Coders.
    return []

  def as_cloud_object(self):
    """Returns Google Cloud Dataflow API description of this coder."""
    # This is an internal detail of the Coder API and does not need to be
    # refined in user-defined Coders.

    value = {
        # We pass coders in the form "<coder_name>$<pickled_data>" to make the
        # job description JSON more readable.  Data before the $ is ignored by
        # the worker.
        '@type': serialize_coder(self),
        'component_encodings': list(
            component.as_cloud_object()
            for component in self._get_component_coders()
        ),
    }
    return value

  def __repr__(self):
    return self.__class__.__name__

  def __eq__(self, other):
    # pylint: disable=protected-access
    return (self.__class__ == other.__class__
            and self._dict_without_impl() == other._dict_without_impl())
    # pylint: enable=protected-access

  def to_runner_api(self, context):
    # TODO(BEAM-115): Use specialized URNs and components.
    from apache_beam.runners.api import beam_runner_api_pb2
    return beam_runner_api_pb2.Coder(
        spec=beam_runner_api_pb2.SdkFunctionSpec(
            spec=beam_runner_api_pb2.FunctionSpec(
                urn=urns.PICKLED_CODER,
                parameter=proto_utils.pack_Any(
                    google.protobuf.wrappers_pb2.BytesValue(
                        value=serialize_coder(self))))))

  @staticmethod
  def from_runner_api(proto, context):
    any_proto = proto.spec.spec.parameter
    bytes_proto = google.protobuf.wrappers_pb2.BytesValue()
    any_proto.Unpack(bytes_proto)
    return deserialize_coder(bytes_proto.value)


class StrUtf8Coder(Coder):
  """A coder used for reading and writing strings as UTF-8."""

  def encode(self, value):
    return value.encode('utf-8')

  def decode(self, value):
    return value.decode('utf-8')

  def is_deterministic(self):
    return True


class ToStringCoder(Coder):
  """A default string coder used if no sink coder is specified."""

  def encode(self, value):
    if isinstance(value, unicode):
      return value.encode('utf-8')
    elif isinstance(value, str):
      return value
    return str(value)

  def decode(self, _):
    raise NotImplementedError('ToStringCoder cannot be used for decoding.')

  def is_deterministic(self):
    return True


class FastCoder(Coder):
  """Coder subclass used when a (faster) CoderImpl is supplied directly.

  The Coder class defines _create_impl in terms of encode() and decode();
  this class inverts that by defining encode() and decode() in terms of
  _create_impl().
  """

  def encode(self, value):
    """Encodes the given object into a byte string."""
    return self.get_impl().encode(value)

  def decode(self, encoded):
    """Decodes the given byte string into the corresponding object."""
    return self.get_impl().decode(encoded)

  def estimate_size(self, value):
    return self.get_impl().estimate_size(value)

  def _create_impl(self):
    raise NotImplementedError


class BytesCoder(FastCoder):
  """Byte string coder."""

  def _create_impl(self):
    return coder_impl.BytesCoderImpl()

  def is_deterministic(self):
    return True

  def __eq__(self, other):
    return type(self) == type(other)

  def __hash__(self):
    return hash(type(self))


class VarIntCoder(FastCoder):
  """Variable-length integer coder."""

  def _create_impl(self):
    return coder_impl.VarIntCoderImpl()

  def is_deterministic(self):
    return True

  def __eq__(self, other):
    return type(self) == type(other)

  def __hash__(self):
    return hash(type(self))


class FloatCoder(FastCoder):
  """A coder used for floating-point values."""

  def _create_impl(self):
    return coder_impl.FloatCoderImpl()

  def is_deterministic(self):
    return True

  def __eq__(self, other):
    return type(self) == type(other)

  def __hash__(self):
    return hash(type(self))


class TimestampCoder(FastCoder):
  """A coder used for timeutil.Timestamp values."""

  def _create_impl(self):
    return coder_impl.TimestampCoderImpl()

  def is_deterministic(self):
    return True

  def __eq__(self, other):
    return type(self) == type(other)

  def __hash__(self):
    return hash(type(self))


class SingletonCoder(FastCoder):
  """A coder that always encodes exactly one value."""

  def __init__(self, value):
    self._value = value

  def _create_impl(self):
    return coder_impl.SingletonCoderImpl(self._value)

  def is_deterministic(self):
    return True

  def __eq__(self, other):
    return type(self) == type(other) and self._value == other._value

  def __hash__(self):
    return hash(self._value)


def maybe_dill_dumps(o):
  """Pickle using cPickle or the Dill pickler as a fallback."""
  # We need to use the dill pickler for objects of certain custom classes,
  # including, for example, ones that contain lambdas.
  try:
    return pickle.dumps(o)
  except Exception:  # pylint: disable=broad-except
    return dill.dumps(o)


def maybe_dill_loads(o):
  """Unpickle using cPickle or the Dill pickler as a fallback."""
  try:
    return pickle.loads(o)
  except Exception:  # pylint: disable=broad-except
    return dill.loads(o)


class _PickleCoderBase(FastCoder):
  """Base class for pickling coders."""

  def is_deterministic(self):
    # Note that the default coder, the PickleCoder, is not deterministic (for
    # example, the ordering of picked entries in maps may vary across
    # executions), and so is not in general suitable for usage as a key coder in
    # GroupByKey operations.
    return False

  def as_cloud_object(self, is_pair_like=True):
    value = super(_PickleCoderBase, self).as_cloud_object()
    # We currently use this coder in places where we cannot infer the coder to
    # use for the value type in a more granular way.  In places where the
    # service expects a pair, it checks for the "is_pair_like" key, in which
    # case we would fail without the hack below.
    if is_pair_like:
      value['is_pair_like'] = True
      value['component_encodings'] = [
          self.as_cloud_object(is_pair_like=False),
          self.as_cloud_object(is_pair_like=False)
      ]

    return value

  # We allow .key_coder() and .value_coder() to be called on PickleCoder since
  # we can't always infer the return values of lambdas in ParDo operations, the
  # result of which may be used in a GroupBykey.
  def is_kv_coder(self):
    return True

  def key_coder(self):
    return self

  def value_coder(self):
    return self

  def __eq__(self, other):
    return type(self) == type(other)

  def __hash__(self):
    return hash(type(self))


class PickleCoder(_PickleCoderBase):
  """Coder using Python's pickle functionality."""

  def _create_impl(self):
    return coder_impl.CallbackCoderImpl(pickle.dumps, pickle.loads)


class DillCoder(_PickleCoderBase):
  """Coder using dill's pickle functionality."""

  def _create_impl(self):
    return coder_impl.CallbackCoderImpl(maybe_dill_dumps, maybe_dill_loads)


class DeterministicFastPrimitivesCoder(FastCoder):
  """Throws runtime errors when encoding non-deterministic values."""

  def __init__(self, coder, step_label):
    self._underlying_coder = coder
    self._step_label = step_label

  def _create_impl(self):
    return coder_impl.DeterministicFastPrimitivesCoderImpl(
        self._underlying_coder.get_impl(), self._step_label)

  def is_deterministic(self):
    return True

  def is_kv_coder(self):
    return True

  def key_coder(self):
    return self

  def value_coder(self):
    return self


class FastPrimitivesCoder(FastCoder):
  """Encodes simple primitives (e.g. str, int) efficiently.

  For unknown types, falls back to another coder (e.g. PickleCoder).
  """
  def __init__(self, fallback_coder=PickleCoder()):
    self._fallback_coder = fallback_coder

  def _create_impl(self):
    return coder_impl.FastPrimitivesCoderImpl(
        self._fallback_coder.get_impl())

  def is_deterministic(self):
    return self._fallback_coder.is_deterministic()

  def as_cloud_object(self, is_pair_like=True):
    value = super(FastCoder, self).as_cloud_object()
    # We currently use this coder in places where we cannot infer the coder to
    # use for the value type in a more granular way.  In places where the
    # service expects a pair, it checks for the "is_pair_like" key, in which
    # case we would fail without the hack below.
    if is_pair_like:
      value['is_pair_like'] = True
      value['component_encodings'] = [
          self.as_cloud_object(is_pair_like=False),
          self.as_cloud_object(is_pair_like=False)
      ]

    return value

  # We allow .key_coder() and .value_coder() to be called on FastPrimitivesCoder
  # since we can't always infer the return values of lambdas in ParDo
  # operations, the result of which may be used in a GroupBykey.
  def is_kv_coder(self):
    return True

  def key_coder(self):
    return self

  def value_coder(self):
    return self

  def __eq__(self, other):
    return type(self) == type(other)

  def __hash__(self):
    return hash(type(self))


class Base64PickleCoder(Coder):
  """Coder of objects by Python pickle, then base64 encoding."""
  # TODO(robertwb): Do base64 encoding where it's needed (e.g. in json) rather
  # than via a special Coder.

  def encode(self, value):
    return base64.b64encode(pickle.dumps(value))

  def decode(self, encoded):
    return pickle.loads(base64.b64decode(encoded))

  def is_deterministic(self):
    # Note that the Base64PickleCoder is not deterministic.  See the
    # corresponding comments for PickleCoder above.
    return False

  # We allow .key_coder() and .value_coder() to be called on Base64PickleCoder
  # since we can't always infer the return values of lambdas in ParDo
  # operations, the result of which may be used in a GroupBykey.
  #
  # TODO(ccy): this is currently only used for KV values from Create transforms.
  # Investigate a way to unify this with PickleCoder.
  def is_kv_coder(self):
    return True

  def key_coder(self):
    return self

  def value_coder(self):
    return self


class ProtoCoder(FastCoder):
  """A Coder for Google Protocol Buffers.

  It supports both Protocol Buffers syntax versions 2 and 3. However,
  the runtime version of the python protobuf library must exactly match the
  version of the protoc compiler what was used to generate the protobuf
  messages.

  ProtoCoder is registered in the global CoderRegistry as the default coder for
  any protobuf Message object.

  """

  def __init__(self, proto_message_type):
    self.proto_message_type = proto_message_type

  def _create_impl(self):
    return coder_impl.ProtoCoderImpl(self.proto_message_type)

  def is_deterministic(self):
    # TODO(vikasrk): A proto message can be deterministic if it does not contain
    # a Map.
    return False

  def __eq__(self, other):
    return (type(self) == type(other)
            and self.proto_message_type == other.proto_message_type)

  def __hash__(self):
    return hash(self.proto_message_type)

  @staticmethod
  def from_type_hint(typehint, unused_registry):
    if issubclass(typehint, google.protobuf.message.Message):
      return ProtoCoder(typehint)
    else:
      raise ValueError(('Expected a subclass of google.protobuf.message.Message'
                        ', but got a %s' % typehint))


class TupleCoder(FastCoder):
  """Coder of tuple objects."""

  def __init__(self, components):
    self._coders = tuple(components)

  def _create_impl(self):
    return coder_impl.TupleCoderImpl([c.get_impl() for c in self._coders])

  def is_deterministic(self):
    return all(c.is_deterministic() for c in self._coders)

  @staticmethod
  def from_type_hint(typehint, registry):
    return TupleCoder([registry.get_coder(t) for t in typehint.tuple_types])

  def as_cloud_object(self):
    if self.is_kv_coder():
      return {
          '@type': 'kind:pair',
          'is_pair_like': True,
          'component_encodings': list(
              component.as_cloud_object()
              for component in self._get_component_coders()
          ),
      }

    return super(TupleCoder, self).as_cloud_object()

  def _get_component_coders(self):
    return self.coders()

  def coders(self):
    return self._coders

  def is_kv_coder(self):
    return len(self._coders) == 2

  def key_coder(self):
    if len(self._coders) != 2:
      raise ValueError('TupleCoder does not have exactly 2 components.')
    return self._coders[0]

  def value_coder(self):
    if len(self._coders) != 2:
      raise ValueError('TupleCoder does not have exactly 2 components.')
    return self._coders[1]

  def __repr__(self):
    return 'TupleCoder[%s]' % ', '.join(str(c) for c in self._coders)

  def __eq__(self, other):
    return (type(self) == type(other)
            and self._coders == self._coders)

  def __hash__(self):
    return hash(self._coders)


class TupleSequenceCoder(FastCoder):
  """Coder of homogeneous tuple objects."""

  def __init__(self, elem_coder):
    self._elem_coder = elem_coder

  def _create_impl(self):
    return coder_impl.TupleSequenceCoderImpl(self._elem_coder.get_impl())

  def is_deterministic(self):
    return self._elem_coder.is_deterministic()

  @staticmethod
  def from_type_hint(typehint, registry):
    return TupleSequenceCoder(registry.get_coder(typehint.inner_type))

  def _get_component_coders(self):
    return (self._elem_coder,)

  def __repr__(self):
    return 'TupleSequenceCoder[%r]' % self._elem_coder

  def __eq__(self, other):
    return (type(self) == type(other)
            and self._elem_coder == self._elem_coder)

  def __hash__(self):
    return hash((type(self), self._elem_coder))


class IterableCoder(FastCoder):
  """Coder of iterables of homogeneous objects."""

  def __init__(self, elem_coder):
    self._elem_coder = elem_coder

  def _create_impl(self):
    return coder_impl.IterableCoderImpl(self._elem_coder.get_impl())

  def is_deterministic(self):
    return self._elem_coder.is_deterministic()

  def as_cloud_object(self):
    return {
        '@type': 'kind:stream',
        'is_stream_like': True,
        'component_encodings': [self._elem_coder.as_cloud_object()],
    }

  def value_coder(self):
    return self._elem_coder

  @staticmethod
  def from_type_hint(typehint, registry):
    return IterableCoder(registry.get_coder(typehint.inner_type))

  def _get_component_coders(self):
    return (self._elem_coder,)

  def __repr__(self):
    return 'IterableCoder[%r]' % self._elem_coder

  def __eq__(self, other):
    return (type(self) == type(other)
            and self._elem_coder == self._elem_coder)

  def __hash__(self):
    return hash((type(self), self._elem_coder))


class GlobalWindowCoder(SingletonCoder):
  """Coder for global windows."""

  def __init__(self):
    from apache_beam.transforms import window
    super(GlobalWindowCoder, self).__init__(window.GlobalWindow())

  def as_cloud_object(self):
    return {
        '@type': 'kind:global_window',
    }


class IntervalWindowCoder(FastCoder):
  """Coder for an window defined by a start timestamp and a duration."""

  def _create_impl(self):
    return coder_impl.IntervalWindowCoderImpl()

  def is_deterministic(self):
    return True

  def as_cloud_object(self):
    return {
        '@type': 'kind:interval_window',
    }

  def __eq__(self, other):
    return type(self) == type(other)

  def __hash__(self):
    return hash(type(self))


class WindowedValueCoder(FastCoder):
  """Coder for windowed values."""

  def __init__(self, wrapped_value_coder, window_coder=None):
    if not window_coder:
      window_coder = PickleCoder()
    self.wrapped_value_coder = wrapped_value_coder
    self.timestamp_coder = TimestampCoder()
    self.window_coder = window_coder

  def _create_impl(self):
    return coder_impl.WindowedValueCoderImpl(
        self.wrapped_value_coder.get_impl(),
        self.timestamp_coder.get_impl(),
        self.window_coder.get_impl())

  def is_deterministic(self):
    return all(c.is_deterministic() for c in [self.wrapped_value_coder,
                                              self.timestamp_coder,
                                              self.window_coder])

  def as_cloud_object(self):
    return {
        '@type': 'kind:windowed_value',
        'is_wrapper': True,
        'component_encodings': [
            component.as_cloud_object()
            for component in self._get_component_coders()],
    }

  def _get_component_coders(self):
    return [self.wrapped_value_coder, self.window_coder]

  def is_kv_coder(self):
    return self.wrapped_value_coder.is_kv_coder()

  def key_coder(self):
    return self.wrapped_value_coder.key_coder()

  def value_coder(self):
    return self.wrapped_value_coder.value_coder()

  def __repr__(self):
    return 'WindowedValueCoder[%s]' % self.wrapped_value_coder

  def __eq__(self, other):
    return (type(self) == type(other)
            and self.wrapped_value_coder == other.wrapped_value_coder
            and self.timestamp_coder == other.timestamp_coder
            and self.window_coder == other.window_coder)

  def __hash__(self):
    return hash(
        (self.wrapped_value_coder, self.timestamp_coder, self.window_coder))


class LengthPrefixCoder(FastCoder):
  """Coder which prefixes the length of the encoded object in the stream."""

  def __init__(self, value_coder):
    self._value_coder = value_coder

  def _create_impl(self):
    return coder_impl.LengthPrefixCoderImpl(self._value_coder)

  def is_deterministic(self):
    return self._value_coder.is_deterministic()

  def estimate_size(self, value):
    value_size = self._value_coder.estimate_size(value)
    return get_varint_size(value_size) + value_size

  def value_coder(self):
    return self._value_coder

  def as_cloud_object(self):
    return {
        '@type': 'kind:length_prefix',
        'component_encodings': [self._value_coder.as_cloud_object()],
    }

  def _get_component_coders(self):
    return (self._value_coder,)

  def __repr__(self):
    return 'LengthPrefixCoder[%r]' % self._value_coder

  def __eq__(self, other):
    return (type(self) == type(other)
            and self._value_coder == other._value_coder)

  def __hash__(self):
    return hash((type(self), self._value_coder))
