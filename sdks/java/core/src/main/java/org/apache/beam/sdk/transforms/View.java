/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package org.apache.beam.sdk.transforms;

import java.util.List;
import java.util.Map;
import org.apache.beam.sdk.annotations.Internal;
import org.apache.beam.sdk.coders.Coder;
import org.apache.beam.sdk.coders.CoderException;
import org.apache.beam.sdk.runners.PipelineRunner;
import org.apache.beam.sdk.runners.TransformHierarchy.Node;
import org.apache.beam.sdk.util.CoderUtils;
import org.apache.beam.sdk.values.KV;
import org.apache.beam.sdk.values.PCollection;
import org.apache.beam.sdk.values.PCollectionView;
import org.apache.beam.sdk.values.PCollectionViews;

/**
 * Transforms for creating {@link PCollectionView PCollectionViews} from
 * {@link PCollection PCollections} (to read them as side inputs).
 *
 * <p>While a {@link PCollection PCollection&lt;ElemT&gt;} has many values of type {@code ElemT} per
 * window, a {@link PCollectionView PCollectionView&lt;ViewT&gt;} has a single value of type
 * {@code ViewT} for each window. It can be thought of as a mapping from windows to values of
 * type {@code ViewT}. The transforms here represent ways of converting the {@code ElemT} values
 * in a window into a {@code ViewT} for that window.
 *
 * <p>When a {@link ParDo} tranform is processing a main input
 * element in a window {@code w} and a {@link PCollectionView} is read via
 * {@link DoFn.ProcessContext#sideInput}, the value of the view for {@code w} is
 * returned.
 *
 * <p>The SDK supports viewing a {@link PCollection}, per window, as a single value,
 * a {@link List}, an {@link Iterable}, a {@link Map}, or a multimap (iterable-valued {@link Map}).
 *
 * <p>For a {@link PCollection} that contains a single value of type {@code T}
 * per window, such as the output of {@link Combine#globally},
 * use {@link View#asSingleton()} to prepare it for use as a side input:
 *
 * <pre>
 * {@code
 * PCollectionView<T> output = someOtherPCollection
 *     .apply(Combine.globally(...))
 *     .apply(View.<T>asSingleton());
 * }
 * </pre>
 *
 * <p>For a small {@link PCollection} with windows that can fit entirely in memory,
 * use {@link View#asList()} to prepare it for use as a {@code List}.
 * When read as a side input, the entire list for a window will be cached in memory.
 *
 * <pre>
 * {@code
 * PCollectionView<List<T>> output =
 *    smallPCollection.apply(View.<T>asList());
 * }
 * </pre>
 *
 * <p>If a {@link PCollection} of {@code KV<K, V>} is known to
 * have a single value per window for each key, then use {@link View#asMap()}
 * to view it as a {@code Map<K, V>}:
 *
 * <pre>
 * {@code
 * PCollectionView<Map<K, V> output =
 *     somePCollection.apply(View.<K, V>asMap());
 * }
 * </pre>
 *
 * <p>Otherwise, to access a {@link PCollection} of {@code KV<K, V>} as a
 * {@code Map<K, Iterable<V>>} side input, use {@link View#asMultimap()}:
 *
 * <pre>
 * {@code
 * PCollectionView<Map<K, Iterable<V>> output =
 *     somePCollection.apply(View.<K, Iterable<V>>asMap());
 * }
 * </pre>
 *
 * <p>To iterate over an entire window of a {@link PCollection} via
 * side input, use {@link View#asIterable()}:
 *
 * <pre>
 * {@code
 * PCollectionView<Iterable<T>> output =
 *     somePCollection.apply(View.<T>asIterable());
 * }
 * </pre>
 *
 *
 * <p>Both {@link View#asMultimap()} and {@link View#asMap()} are useful
 * for implementing lookup based "joins" with the main input, when the
 * side input is small enough to fit into memory.
 *
 * <p>For example, if you represent a page on a website via some {@code Page} object and
 * have some type {@code UrlVisits} logging that a URL was visited, you could convert these
 * to more fully structured {@code PageVisit} objects using a side input, something like the
 * following:
 *
 * <pre>
 * {@code
 * PCollection<Page> pages = ... // pages fit into memory
 * PCollection<UrlVisit> urlVisits = ... // very large collection
 * final PCollectionView<Map<URL, Page>> = urlToPage
 *     .apply(WithKeys.of( ... )) // extract the URL from the page
 *     .apply(View.<URL, Page>asMap());
 *
 * PCollection PageVisits = urlVisits
 *     .apply(ParDo.withSideInputs(urlToPage)
 *         .of(new DoFn<UrlVisit, PageVisit>() {
 *             {@literal @}Override
 *             void processElement(ProcessContext context) {
 *               UrlVisit urlVisit = context.element();
 *               Page page = urlToPage.get(urlVisit.getUrl());
 *               c.output(new PageVisit(page, urlVisit.getVisitData()));
 *             }
 *         }));
 * }
 * </pre>
 *
 * <p>See {@link ParDo.SingleOutput#withSideInputs} for details on how to access
 * this variable inside a {@link ParDo} over another {@link PCollection}.
 */
public class View {

  // Do not instantiate
  private View() { }

  /**
   * Returns a {@link AsSingleton} transform that takes a
   * {@link PCollection} with a single value per window
   * as input and produces a {@link PCollectionView} that returns
   * the value in the main input window when read as a side input.
   *
   * <pre>
   * {@code
   * PCollection<InputT> input = ...
   * CombineFn<InputT, OutputT> yourCombineFn = ...
   * PCollectionView<OutputT> output = input
   *     .apply(Combine.globally(yourCombineFn))
   *     .apply(View.<OutputT>asSingleton());
   * }</pre>
   *
   * <p>If the input {@link PCollection} is empty,
   * throws {@link java.util.NoSuchElementException} in the consuming
   * {@link DoFn}.
   *
   * <p>If the input {@link PCollection} contains more than one
   * element, throws {@link IllegalArgumentException} in the
   * consuming {@link DoFn}.
   */
  public static <T> AsSingleton<T> asSingleton() {
    return new AsSingleton<>();
  }

  /**
   * Returns a {@link View.AsList} transform that takes a {@link PCollection} and returns a
   * {@link PCollectionView} mapping each window to a {@link List} containing
   * all of the elements in the window.
   *
   * <p>The resulting list is required to fit in memory.
   */
  public static <T> AsList<T> asList() {
    return new AsList<>();
  }

  /**
   * Returns a {@link View.AsIterable} transform that takes a {@link PCollection} as input
   * and produces a {@link PCollectionView} mapping each window to an
   * {@link Iterable} of the values in that window.
   *
   * <p>The values of the {@link Iterable} for a window are not required to fit in memory,
   * but they may also not be effectively cached. If it is known that every window fits in memory,
   * and stronger caching is desired, use {@link #asList}.
   */
  public static <T> AsIterable<T> asIterable() {
    return new AsIterable<>();
  }

  /**
   * Returns a {@link View.AsMap} transform that takes a
   * {@link PCollection PCollection&lt;KV&lt;K, V&gt;&gt;} as
   * input and produces a {@link PCollectionView} mapping each window to
   * a {@link Map Map&lt;K, V&gt;}. It is required that each key of the input be
   * associated with a single value, per window. If this is not the case, precede this
   * view with {@code Combine.perKey}, as in the example below, or alternatively
   * use {@link View#asMultimap()}.
   *
   * <pre>
   * {@code
   * PCollection<KV<K, V>> input = ...
   * CombineFn<V, OutputT> yourCombineFn = ...
   * PCollectionView<Map<K, OutputT>> output = input
   *     .apply(Combine.perKey(yourCombineFn))
   *     .apply(View.<K, OutputT>asMap());
   * }</pre>
   *
   * <p>Currently, the resulting map is required to fit into memory.
   */
  public static <K, V> AsMap<K, V> asMap() {
    return new AsMap<K, V>();
  }

  /**
   * Returns a {@link View.AsMultimap} transform that takes a
   * {@link PCollection PCollection&lt;KV&lt;K, V&gt;&gt;}
   * as input and produces a {@link PCollectionView} mapping
   * each window to its contents as a {@link Map Map&lt;K, Iterable&lt;V&gt;&gt;}
   * for use as a side input.
   * In contrast to {@link View#asMap()}, it is not required that the keys in the
   * input collection be unique.
   *
   * <pre>
   * {@code
   * PCollection<KV<K, V>> input = ... // maybe more than one occurrence of a some keys
   * PCollectionView<Map<K, V>> output = input.apply(View.<K, V>asMultimap());
   * }</pre>
   *
   * <p>Currently, the resulting map is required to fit into memory.
   */
  public static <K, V> AsMultimap<K, V> asMultimap() {
    return new AsMultimap<K, V>();
  }

  /**
   * <b><i>For internal use only; no backwards-compatibility guarantees.</i></b>
   *
   * <p>Public only so a {@link PipelineRunner} may override its behavior.
   *
   * <p>See {@link View#asList()}.
   */
  @Internal
  public static class AsList<T> extends PTransform<PCollection<T>, PCollectionView<List<T>>> {
    private AsList() { }

    @Override
    public PCollectionView<List<T>> expand(PCollection<T> input) {
      try {
        GroupByKey.applicableTo(input);
      } catch (IllegalStateException e) {
        throw new IllegalStateException("Unable to create a side-input view from input", e);
      }

      return input.apply(CreatePCollectionView.<T, List<T>>of(PCollectionViews.listView(
          input, input.getWindowingStrategy(), input.getCoder())));
    }
  }

  /**
   * <b><i>For internal use only; no backwards-compatibility guarantees.</i></b>
   *
   * <p>Public only so a {@link PipelineRunner} may override its behavior.
   *
   * <p>See {@link View#asIterable()}.
   */
  @Internal
  public static class AsIterable<T>
      extends PTransform<PCollection<T>, PCollectionView<Iterable<T>>> {
    private AsIterable() { }

    @Override
    public PCollectionView<Iterable<T>> expand(PCollection<T> input) {
      try {
        GroupByKey.applicableTo(input);
      } catch (IllegalStateException e) {
        throw new IllegalStateException("Unable to create a side-input view from input", e);
      }

      return input.apply(CreatePCollectionView.<T, Iterable<T>>of(PCollectionViews.iterableView(
          input, input.getWindowingStrategy(), input.getCoder())));
    }
  }

  /**
   * <b><i>For internal use only; no backwards-compatibility guarantees.</i></b>
   *
   * <p>Public only so a {@link PipelineRunner} may override its behavior.
   *
   * <p>See {@link View#asSingleton()}.
   */
  @Internal
  public static class AsSingleton<T> extends PTransform<PCollection<T>, PCollectionView<T>> {
    private final T defaultValue;
    private final boolean hasDefault;

    private AsSingleton() {
      this.defaultValue = null;
      this.hasDefault = false;
    }

    private AsSingleton(T defaultValue) {
      this.defaultValue = defaultValue;
      this.hasDefault = true;
    }

    /**
     * Returns whether this transform has a default value.
     */
    public boolean hasDefaultValue() {
      return hasDefault;
    }

    /**
     * Returns the default value of this transform, or null if there isn't one.
     */
    public T defaultValue() {
      return defaultValue;
    }

    /**
     * Default value to return for windows with no value in them.
     */
    public AsSingleton<T> withDefaultValue(T defaultValue) {
      return new AsSingleton<>(defaultValue);
    }

    @Override
    public PCollectionView<T> expand(PCollection<T> input) {
      try {
        GroupByKey.applicableTo(input);
      } catch (IllegalStateException e) {
        throw new IllegalStateException("Unable to create a side-input view from input", e);
      }

      Combine.Globally<T, T> singletonCombine =
          Combine.globally(new SingletonCombineFn<>(hasDefault, input.getCoder(), defaultValue));
      if (!hasDefault) {
        singletonCombine = singletonCombine.withoutDefaults();
      }
      return input.apply(singletonCombine.asSingletonView());
    }
  }

  private static class SingletonCombineFn<T> extends Combine.BinaryCombineFn<T> {
    private final boolean hasDefault;
    private final Coder<T> valueCoder;
    private final byte[] defaultValue;

    private SingletonCombineFn(boolean hasDefault, Coder<T> coder, T defaultValue) {
      this.hasDefault = hasDefault;
      if (hasDefault) {
        if (defaultValue == null) {
          this.defaultValue = null;
          this.valueCoder = coder;
        } else {
          this.valueCoder = coder;
          try {
            this.defaultValue = CoderUtils.encodeToByteArray(coder, defaultValue);
          } catch (CoderException e) {
            throw new IllegalArgumentException(
                String.format(
                    "Could not encode the default value %s with the provided coder %s",
                    defaultValue, coder));
          }
        }
      } else {
        this.valueCoder = null;
        this.defaultValue = null;
      }
    }

    @Override
    public T apply(T left, T right) {
      throw new IllegalArgumentException(
          "PCollection with more than one element "
              + "accessed as a singleton view. Consider using Combine.globally().asSingleton() to "
              + "combine the PCollection into a single value");
    }

    public T identity() {
      if (hasDefault) {
        if (defaultValue == null) {
          return null;
        }
        try {
          return CoderUtils.decodeFromByteArray(valueCoder, defaultValue);
        } catch (CoderException e) {
          throw new IllegalArgumentException(
              String.format(
                  "Could not decode the default value with the provided coder %s", valueCoder));
        }
      } else {
        throw new IllegalArgumentException(
            "Empty PCollection accessed as a singleton view. "
                + "Consider setting withDefault to provide a default value");
      }
    }
  }

  /**
   * <b><i>For internal use only; no backwards-compatibility guarantees.</i></b>
   *
   * <p>Public only so a {@link PipelineRunner} may override its behavior.
   *
   * <p>See {@link View#asMultimap()}.
   */
  @Internal
  public static class AsMultimap<K, V>
      extends PTransform<PCollection<KV<K, V>>, PCollectionView<Map<K, Iterable<V>>>> {
    private AsMultimap() { }

    @Override
    public PCollectionView<Map<K, Iterable<V>>> expand(PCollection<KV<K, V>> input) {
      try {
        GroupByKey.applicableTo(input);
      } catch (IllegalStateException e) {
        throw new IllegalStateException("Unable to create a side-input view from input", e);
      }

      return input.apply(CreatePCollectionView.<KV<K, V>, Map<K, Iterable<V>>>of(
          PCollectionViews.multimapView(
              input,
              input.getWindowingStrategy(),
              input.getCoder())));
    }
  }

  /**
   * <b><i>For internal use only; no backwards-compatibility guarantees.</i></b>
   *
   * <p>Public only so a {@link PipelineRunner} may override its behavior.
   *
   * <p>See {@link View#asMap()}.
   */
  @Internal
  public static class AsMap<K, V>
      extends PTransform<PCollection<KV<K, V>>, PCollectionView<Map<K, V>>> {
    private AsMap() { }

    /**
     * @deprecated this method simply returns this AsMap unmodified
     */
    @Deprecated()
    public AsMap<K, V> withSingletonValues() {
      return this;
    }

    @Override
    public PCollectionView<Map<K, V>> expand(PCollection<KV<K, V>> input) {
      try {
        GroupByKey.applicableTo(input);
      } catch (IllegalStateException e) {
        throw new IllegalStateException("Unable to create a side-input view from input", e);
      }

      return input.apply(CreatePCollectionView.<KV<K, V>, Map<K, V>>of(
          PCollectionViews.mapView(
              input,
              input.getWindowingStrategy(),
              input.getCoder())));
    }
  }

  ////////////////////////////////////////////////////////////////////////////
  // Internal details below

  /**
   * <b><i>For internal use only; no backwards-compatibility guarantees.</i></b>
   *
   * <p>Creates a primitive {@link PCollectionView}.
   *
   * @param <ElemT> The type of the elements of the input PCollection
   * @param <ViewT> The type associated with the {@link PCollectionView} used as a side input
   */
  @Internal
  public static class CreatePCollectionView<ElemT, ViewT>
      extends PTransform<PCollection<ElemT>, PCollectionView<ViewT>> {
    private PCollectionView<ViewT> view;

    private CreatePCollectionView(PCollectionView<ViewT> view) {
      this.view = view;
    }

    public static <ElemT, ViewT> CreatePCollectionView<ElemT, ViewT> of(
        PCollectionView<ViewT> view) {
      return new CreatePCollectionView<>(view);
    }

    /**
     * Return the {@link PCollectionView} that is returned by applying this {@link PTransform}.
     *
     * <p>This should not be used to obtain the output of any given application of this
     * {@link PTransform}. That should be obtained by inspecting the {@link Node}
     * that contains this {@link CreatePCollectionView}, as this view may have been replaced within
     * pipeline surgery.
     */
    @Deprecated
    public PCollectionView<ViewT> getView() {
      return view;
    }

    @Override
    public PCollectionView<ViewT> expand(PCollection<ElemT> input) {
      return view;
    }
  }
}
